import struct
import unittest

from forge_integrations.errors import ErrorCode, ForgeError
from forge_integrations.voice.audio import (
    AudioFormat,
    parse_wav,
    sniff_audio_format,
    validate_audio_upload,
)
from forge_integrations.voice.languages import resolve_asr_language

from .helpers import HAVE_FFMPEG, ffmpeg_sample, make_wav


def raises_code(test: unittest.TestCase, code: str, func, *args, **kwargs) -> ForgeError:
    with test.assertRaises(ForgeError) as caught:
        func(*args, **kwargs)
    test.assertEqual(caught.exception.code, code)
    return caught.exception


class WavParsingTests(unittest.TestCase):
    def test_duration_from_header(self):
        info = parse_wav(make_wav(seconds=2.5, rate=16000))
        self.assertIsNotNone(info)
        self.assertEqual((info.channels, info.sample_rate, info.bits_per_sample), (1, 16000, 16))
        self.assertAlmostEqual(info.duration_seconds, 2.5, places=3)

    def test_stereo_44k_duration(self):
        info = parse_wav(make_wav(seconds=1.0, rate=44100, channels=2))
        self.assertAlmostEqual(info.duration_seconds, 1.0, places=3)
        self.assertEqual(info.channels, 2)

    def test_streaming_writer_sizes_are_handled(self):
        wav = bytearray(make_wav(seconds=1.0))
        data_pos = bytes(wav).index(b"data")
        for size in (0, 0xFFFFFFFF):
            patched = bytearray(wav)
            patched[data_pos + 4 : data_pos + 8] = struct.pack("<I", size)
            info = parse_wav(bytes(patched))
            self.assertAlmostEqual(info.duration_seconds, 1.0, places=2)

    def test_truncated_data_chunk_uses_what_exists(self):
        wav = make_wav(seconds=2.0)
        info = parse_wav(wav[: len(wav) // 2])
        self.assertLess(info.duration_seconds, 1.1)

    def test_oversized_claimed_data_does_not_inflate_duration(self):
        wav = bytearray(make_wav(seconds=1.0))
        data_pos = bytes(wav).index(b"data")
        wav[data_pos + 4 : data_pos + 8] = struct.pack("<I", 0x7FFFFFF0)
        self.assertAlmostEqual(parse_wav(bytes(wav)).duration_seconds, 1.0, places=2)

    def test_malformed_headers_return_none(self):
        good = make_wav(seconds=0.1)
        self.assertIsNone(parse_wav(b""))
        self.assertIsNone(parse_wav(good[:20]))  # cuts inside the fmt chunk
        self.assertIsNone(parse_wav(b"RIFF" + b"\x00" * 4 + b"WAVE"))  # no chunks
        bad_channels = bytearray(good)
        bad_channels[22:24] = struct.pack("<H", 0)
        self.assertIsNone(parse_wav(bytes(bad_channels)))
        bad_rate = bytearray(good)
        bad_rate[24:28] = struct.pack("<I", 5)
        self.assertIsNone(parse_wav(bytes(bad_rate)))

    def test_chunk_walk_is_bounded(self):
        header = b"RIFF" + struct.pack("<I", 100000) + b"WAVE"
        filler = b"".join(b"JUNK" + struct.pack("<I", 0) for _ in range(1000))
        self.assertIsNone(parse_wav(header + filler))


class SniffTests(unittest.TestCase):
    def test_real_containers_are_identified(self):
        self.assertIs(sniff_audio_format(make_wav()), AudioFormat.WAV)

    def test_ffmpeg_generated_containers(self):
        expected = {
            "mp3": AudioFormat.MP3,
            "mp3_noid3": AudioFormat.MP3,
            "ogg_vorbis": AudioFormat.OGG,
            "ogg_opus": AudioFormat.OGG,
            "flac": AudioFormat.FLAC,
            "m4a": AudioFormat.MP4,
            "webm_opus": AudioFormat.WEBM,
            "wav_stereo44": AudioFormat.WAV,
        }
        for kind, fmt in expected.items():
            with self.subTest(kind=kind):
                self.assertIs(sniff_audio_format(ffmpeg_sample(kind)), fmt)

    def test_non_audio_is_rejected(self):
        samples = {
            "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
            "pdf": b"%PDF-1.7\n" + b"x" * 64,
            "zip": b"PK\x03\x04" + b"\x00" * 64,
            "elf": b"\x7fELF" + b"\x00" * 64,
            "html": b"<!doctype html><script>alert(1)</script>" + b" " * 32,
            "svg": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>" + b" " * 32,
            "shell": b"#!/bin/sh\nrm -rf /\n" + b" " * 32,
            "text": b"just some text pretending to be audio" * 3,
            "tiny": b"RIFF",
            "riff_avi": b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 32,
            "ftyp_huge_box": b"\xff\xff\xff\xffftypM4A " + b"\x00" * 32,
        }
        for name, data in samples.items():
            with self.subTest(name=name):
                self.assertIsNone(sniff_audio_format(data))

    def test_mp3_frame_sync_checks_reject_look_alikes(self):
        self.assertIsNone(sniff_audio_format(b"\xff\xe0\x00\x00" + b"\x00" * 32))  # layer 0 is reserved
        self.assertIsNone(sniff_audio_format(b"\xff\xfb\xf0\x00" + b"\x00" * 32))  # bitrate index 15 is invalid
        self.assertIsNone(sniff_audio_format(b"ID3\x09\x00\x00\x00\x00\x00\x00" + b"\x00" * 32))  # bad ID3 version
        self.assertIs(sniff_audio_format(b"\xff\xfb\x90\x00" + b"\x00" * 32), AudioFormat.MP3)


class ValidateUploadTests(unittest.TestCase):
    def test_accepts_wav_and_reports_facts(self):
        data = make_wav(seconds=3.0)
        result = validate_audio_upload(data, "audio/wav")
        self.assertIs(result.format, AudioFormat.WAV)
        self.assertEqual(result.size_bytes, len(data))
        self.assertEqual(len(result.sha256), 64)
        self.assertAlmostEqual(result.duration_seconds, 3.0, places=2)

    def test_declared_type_may_be_missing_generic_or_carry_parameters(self):
        data = make_wav()
        for declared in (None, "", "application/octet-stream", "audio/wav; codecs=1", "AUDIO/X-WAV", "audio/wave"):
            with self.subTest(declared=declared):
                validate_audio_upload(data, declared)

    def test_declared_type_must_agree_with_the_signature(self):
        data = make_wav()
        for declared in ("audio/mpeg", "image/png", "text/html", "audio/ogg", "video/mp4"):
            with self.subTest(declared=declared):
                raises_code(self, ErrorCode.AUDIO_SIGNATURE_MISMATCH, validate_audio_upload, data, declared)

    def test_empty_and_oversized(self):
        raises_code(self, ErrorCode.AUDIO_EMPTY, validate_audio_upload, b"")
        err = raises_code(self, ErrorCode.AUDIO_TOO_LARGE, validate_audio_upload, make_wav(2.0), None, max_bytes=1000)
        self.assertEqual(err.http_status, 413)

    def test_size_is_checked_before_any_parsing(self):
        # A huge non-audio blob is refused for size, without being inspected further.
        raises_code(self, ErrorCode.AUDIO_TOO_LARGE, validate_audio_upload, b"x" * 5000, None, max_bytes=1000)

    def test_unsupported_content(self):
        err = raises_code(self, ErrorCode.AUDIO_UNSUPPORTED_TYPE, validate_audio_upload, b"%PDF-1.7\n" + b"x" * 64, "audio/wav")
        self.assertEqual(err.http_status, 415)

    def test_wav_duration_limit_is_enforced_from_the_header(self):
        validate_audio_upload(make_wav(seconds=30.0), "audio/wav", max_seconds=30)
        err = raises_code(self, ErrorCode.AUDIO_TOO_LONG, validate_audio_upload, make_wav(seconds=31.0), "audio/wav", max_seconds=30)
        self.assertEqual(err.details, {"max_seconds": 30})
        self.assertEqual(err.http_status, 422)

    def test_corrupt_wav_header(self):
        broken = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"junk" * 16
        raises_code(self, ErrorCode.AUDIO_CORRUPT, validate_audio_upload, broken, "audio/wav")

    def test_compressed_formats_pass_validation_without_a_duration(self):
        for kind in ("mp3", "ogg_opus", "flac", "m4a", "webm_opus"):
            with self.subTest(kind=kind):
                result = validate_audio_upload(ffmpeg_sample(kind), None)
                self.assertIsNone(result.duration_seconds)

    def test_extension_and_filename_play_no_part(self):
        # The API takes bytes and a declared type only. A WAV is a WAV whatever it is called.
        self.assertIs(validate_audio_upload(make_wav(), None).format, AudioFormat.WAV)


class LanguageTests(unittest.TestCase):
    def test_codes_names_and_case(self):
        self.assertEqual(resolve_asr_language("yo").repo_id, "NCAIR1/Yoruba-ASR")
        self.assertEqual(resolve_asr_language(" Hausa ").language, "ha")
        self.assertEqual(resolve_asr_language("IG").repo_id, "NCAIR1/Igbo-ASR")
        self.assertEqual(resolve_asr_language("english").repo_id, "NCAIR1/NigerianAccentedEnglish")
        self.assertEqual(resolve_asr_language("Nigerian English").language, "en")

    def test_unsupported_values(self):
        for value in ("fr", "", None, 5, "pidgin", "yo-NG", "../../etc/passwd"):
            with self.subTest(value=value):
                err = raises_code(self, ErrorCode.LANGUAGE_UNSUPPORTED, resolve_asr_language, value)
                self.assertEqual(err.http_status, 422)
                self.assertEqual(err.details, {"supported": ["en", "ha", "ig", "yo"]})


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
class FixtureSanityTests(unittest.TestCase):
    def test_fixtures_are_real_and_non_trivial(self):
        for kind in ("mp3", "ogg_vorbis", "flac", "m4a", "webm_opus"):
            self.assertGreater(len(ffmpeg_sample(kind)), 1000, kind)


if __name__ == "__main__":
    unittest.main()
