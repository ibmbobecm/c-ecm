import { Icon, type IconName } from "../icons";

export function EmptyState({
  icon,
  title,
  subtitle,
  cta,
}: {
  icon: IconName;
  title: string;
  subtitle: string;
  cta?: { label: string; onClick: () => void };
}) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">
        <Icon name={icon} size={64} />
      </div>
      <h3>{title}</h3>
      <p>{subtitle}</p>
      {cta && (
        <button className="btn-primary" onClick={cta.onClick}>
          {cta.label}
        </button>
      )}
    </div>
  );
}
