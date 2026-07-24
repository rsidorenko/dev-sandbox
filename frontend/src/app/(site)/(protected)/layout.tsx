// AuthProvider задаётся выше — в (site)/layout.tsx, поэтому здесь простой
// passthrough. Route-group служит только маркером защищённых маршрутов
// (см. middleware.ts) и хранит страницы /dashboard и /payment/*.
export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
