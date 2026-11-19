const paths = {
  game: <><path d="M7 8h10a4 4 0 0 1 4 4l1 6a2 2 0 0 1-3.4 1.4L16 17H8l-2.6 2.4A2 2 0 0 1 2 18l1-6a4 4 0 0 1 4-4Z"/><path d="M7 11v6m-3-3h6m7-2h.01m3 3h.01"/></>,
  software: <><rect x="3" y="3" width="18" height="13" rx="2"/><path d="M8 21h8m-4-5v5m-5-14h10"/></>,
  used: <><path d="M4 8h16l1 13H3L4 8Z"/><path d="M8 8V6a4 4 0 0 1 8 0v2m-8 5c1 3 7 3 8 0"/></>,
  heart: <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>,
  bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9m-11 12a2 2 0 0 0 4 0"/></>,
  settings: <><path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3"/><circle cx="16" cy="17" r="3"/></>,
  arrow: <path d="m8 4 8 8-8 8"/>,
  sparkle: <><path d="m12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3 3-6Z"/><path d="M20 2v4m-2-2h4"/></>,
};

export default function Icon({ name, size = 20 }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[name] || paths.arrow}</svg>;
}
