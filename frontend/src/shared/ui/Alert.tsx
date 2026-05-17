type Variant = "error" | "info";

type Props = {
  variant?: Variant;
  className?: string;
  children: React.ReactNode;
};

const styles: Record<Variant, string> = {
  error:
    "rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400",
  info: "rounded-lg bg-brand-50 px-4 py-3 text-sm dark:bg-brand-950/30",
};

export function Alert({ variant = "error", className = "", children }: Props) {
  return <div className={`${styles[variant]} ${className}`}>{children}</div>;
}
