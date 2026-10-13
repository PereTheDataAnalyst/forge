import os
import stat
import tempfile
import unittest
from pathlib import Path

from forge_integrations.errors import ErrorCode, ForgeError
from forge_integrations.security.safe_files import SecureTempStore
from forge_integrations.voice.audio import AudioFormat, sniff_audio_format, suffix_for
from forge_integrations.voice.transcode import (
    BYTES_PER_SECOND,
    build_ffmpeg_command,
    decode_to_pcm16k,
    ffmpeg_available,
)

from .helpers import HAVE_FFMPEG, POSIX, ffmpeg_sample, make_wav


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class DecodeRealAudioTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SecureTempStore(Path(self._tmp.name) / "work")

    def tearDown(self):
        self._tmp.cleanup()

    def decode(self, data: bytes, **kwargs):
        fmt = sniff_audio_format(data)
        self.assertIsNotNone(fmt)
        with self.store.write(data, suffix_for(fmt)) as path:
            return decode_to_pcm16k(path, fmt, **kwargs)

    def test_every_supported_container_decodes_to_16k_mono(self):
        for kind in ("mp3", "mp3_noid3", "ogg_vorbis", "ogg_opus", "flac", "m4a", "webm_opus", "wav_stereo44"):
            with self.subTest(kind=kind):
                decoded = self.decode(ffmpeg_sample(kind, seconds=1.0))
                self.assertEqual(decoded.sample_rate, 16000)
                # Codec padding and priming change the length slightly; one second give or take 150 ms.
                self.assertAlmostEqual(decoded.duration_seconds, 1.0, delta=0.15)
                self.assertEqual(len(decoded.pcm_s16le) % 2, 0)
                self.assertAlmostEqual(len(decoded.pcm_s16le) / BYTES_PER_SECOND, decoded.duration_seconds, delta=0.01)

    def test_decoded_audio_is_not_silence(self):
        decoded = self.decode(make_wav(seconds=1.0, rate=16000, amplitude=0.5))
        samples = memoryview(decoded.pcm_s16le).cast("h")
        self.assertGreater(max(abs(s) for s in samples), 10000)

    def test_audio_over_the_limit_is_rejected_even_when_the_container_hides_the_length(self):
        long_mp3 = ffmpeg_sample("mp3", seconds=40.0)
        with self.assertRaises(ForgeError) as caught:
            self.decode(long_mp3, max_seconds=30)
        self.assertEqual(caught.exception.code, ErrorCode.AUDIO_TOO_LONG)
        self.assertEqual(caught.exception.http_status, 422)

    def test_decode_never_produces_more_than_the_cap(self):
        # 60 seconds of PCM would be ~1.9 MB. The command caps duration and size at the source.
        with self.assertRaises(ForgeError) as caught:
            self.decode(make_wav(seconds=60.0, rate=16000), max_seconds=30)
        self.assertEqual(caught.exception.code, ErrorCode.AUDIO_TOO_LONG)

    def test_garbage_that_looks_like_mp3_is_corrupt_not_a_crash(self):
        garbage = b"ID3\x03\x00\x00\x00\x00\x00\x00" + os.urandom(2048)
        with self.assertRaises(ForgeError) as caught:
            self.decode(garbage)
        self.assertEqual(caught.exception.code, ErrorCode.AUDIO_CORRUPT)

    def test_truncated_file_is_handled(self):
        flac = ffmpeg_sample("flac", seconds=2.0)
        try:
            decoded = self.decode(flac[: len(flac) // 3])
            self.assertLess(decoded.duration_seconds, 2.0)
        except ForgeError as exc:
            self.assertIn(exc.code, {ErrorCode.AUDIO_CORRUPT})

    def test_a_forced_demuxer_stops_playlist_and_reference_tricks(self):
        # Without a forced demuxer ffmpeg would auto-detect this as a concat script and read the
        # referenced file. The demuxer is forced from the sniffed container, so it cannot.
        secret = Path(self._tmp.name) / "secret.txt"
        secret.write_text("TOP-SECRET-MARKER")
        script = f"ffconcat version 1.0\nfile '{secret}'\n".encode()
        with self.store.write(script, ".wav") as path:
            with self.assertRaises(ForgeError) as caught:
                decode_to_pcm16k(path, AudioFormat.WAV)
        self.assertEqual(caught.exception.code, ErrorCode.AUDIO_CORRUPT)
        self.assertNotIn("TOP-SECRET-MARKER", str(caught.exception.message))

    def test_error_messages_do_not_leak_paths_or_ffmpeg_output(self):
        with self.store.write(b"RIFF" + b"\x00" * 200, ".wav") as path:
            with self.assertRaises(ForgeError) as caught:
                decode_to_pcm16k(path, AudioFormat.WAV)
        text = caught.exception.message + str(caught.exception.details)
        self.assertNotIn(str(path), text)
        self.assertNotIn("ffmpeg", text.lower())


class CommandHardeningTests(unittest.TestCase):
    def test_command_shape(self):
        command = build_ffmpeg_command(Path("/tmp/x.wav"), AudioFormat.WAV, max_seconds=30)
        joined = " ".join(command)
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-nostdin", command)
        self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file")
        self.assertEqual(command[command.index("-f") + 1], "wav")  # forced input demuxer
        self.assertEqual(command[-1], "pipe:1")
        self.assertIn("-ar 16000", joined)
        self.assertIn("-ac 1", joined)
        self.assertIn("-fs", command)
        self.assertEqual(command.count("-t"), 2)  # capped on input and on output
        self.assertTrue(all(isinstance(part, str) for part in command))

    def test_every_container_has_a_fixed_demuxer(self):
        forced = {
            fmt: build_ffmpeg_command(Path("/tmp/x"), fmt, max_seconds=30)
            for fmt in AudioFormat
        }
        names = {fmt: cmd[cmd.index("-f") + 1] for fmt, cmd in forced.items()}
        self.assertEqual(
            names,
            {
                AudioFormat.WAV: "wav",
                AudioFormat.MP3: "mp3",
                AudioFormat.OGG: "ogg",
                AudioFormat.FLAC: "flac",
                AudioFormat.MP4: "mov",
                AudioFormat.WEBM: "matroska",
            },
        )

    def test_missing_ffmpeg_is_a_clear_service_error(self):
        self.assertFalse(ffmpeg_available("definitely-not-installed-ffmpeg"))
        with self.assertRaises(ForgeError) as caught:
            decode_to_pcm16k(Path("/tmp/x.wav"), AudioFormat.WAV, ffmpeg_path="definitely-not-installed-ffmpeg")
        self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_UNAVAILABLE)
        self.assertEqual(caught.exception.http_status, 503)

    @unittest.skipUnless(POSIX, "needs a POSIX shell")
    def test_timeout_kills_the_decoder_and_reports_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "slow-ffmpeg"
            fake.write_text("#!/bin/sh\nsleep 5\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            with self.assertRaises(ForgeError) as caught:
                decode_to_pcm16k(Path(tmp) / "in.wav", AudioFormat.WAV, timeout_seconds=0.4, ffmpeg_path=str(fake))
        self.assertEqual(caught.exception.code, ErrorCode.PROVIDER_TIMEOUT)
        self.assertEqual(caught.exception.http_status, 504)

    @unittest.skipUnless(POSIX, "needs a POSIX shell")
    def test_decoder_failure_exit_code_is_audio_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "failing-ffmpeg"
            fake.write_text("#!/bin/sh\necho 'secret path /etc/passwd' >&2\nexit 1\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            with self.assertRaises(ForgeError) as caught:
                decode_to_pcm16k(Path(tmp) / "in.wav", AudioFormat.WAV, ffmpeg_path=str(fake))
        self.assertEqual(caught.exception.code, ErrorCode.AUDIO_CORRUPT)
        self.assertNotIn("passwd", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
