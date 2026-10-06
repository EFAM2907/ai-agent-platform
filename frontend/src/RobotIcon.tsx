interface RobotIconProps {
  size?: number;
  className?: string;
}

// Robot amigable, no un icono de "mensaje" -- pedido explicito: antena
// con punta redonda, cabeza redondeada, orejitas laterales, ojos punto
// y una sonrisa curva. Stroke-based para que escale y recoloree con
// currentColor, sin depender de un asset externo.
export function RobotIcon({ size = 18, className }: RobotIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M12 2.5v3" />
      <circle cx="12" cy="2" r="1" fill="currentColor" stroke="none" />
      <rect x="4" y="8" width="16" height="12" rx="4" />
      <path d="M4 13H2M22 13h-2" />
      <circle cx="9" cy="14" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="15" cy="14" r="1.3" fill="currentColor" stroke="none" />
      <path d="M9 17.2c1.2 1 4.8 1 6 0" />
    </svg>
  );
}
