import Link from "next/link";
import styles from "./builder.module.css";

const SPORTS: { key: string; label: string }[] = [
  { key: "mlb", label: "MLB" },
  { key: "nfl", label: "NFL" },
  { key: "nba", label: "NBA" },
  { key: "mls", label: "MLS" },
];

export default function SportTabs({ active }: { active: string }) {
  return (
    <nav className={styles.sportTabs} aria-label="Sport">
      {SPORTS.map((s) => (
        <Link
          key={s.key}
          href={`/builder?sport=${s.key}`}
          className={s.key === active ? styles.sportTabActive : styles.sportTab}
          aria-current={s.key === active ? "page" : undefined}
        >
          {s.label}
        </Link>
      ))}
    </nav>
  );
}
