export function LogoMark({ size = 34 }) {
  return (
    <span className="lp-logo-mark" style={{ width: size, height: size }}>
      <svg width={size * 0.56} height={size * 0.56} viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M5 12.5l4.5 4.5L19 7.5" />
      </svg>
    </span>
  );
}

export default function Logo({ onClick }) {
  return (
    <button className="lp-logo" onClick={onClick} aria-label="CommitIQ home">
      <LogoMark />
      CommitIQ
    </button>
  );
}
