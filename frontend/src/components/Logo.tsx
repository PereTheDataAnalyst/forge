import { Link } from "react-router-dom";

/** The supplied N-ATLAS Forge mark (the orbit N). Aspect ratio of the source artwork is 945:563. */
export function LogoMark({ height = 28 }: { height?: number }) {
  return (
    <img
      className="logo-mark"
      src="/brand/logo-mark-small.png"
      alt=""
      height={height}
      width={Math.round(height * 1.68)}
    />
  );
}

export function BrandLink({ to = "/" }: { to?: string }) {
  return (
    <Link to={to} className="brand" aria-label="N-ATLAS Forge home">
      <LogoMark />
      <span className="brand__text">
        N-ATLAS <span className="brand__forge">Forge</span>
      </span>
    </Link>
  );
}
