"use client";

import { useRouter } from "next/navigation";
import styles from "./builder.module.css";

export default function RetryButton() {
  const router = useRouter();

  return (
    <button type="button" className={styles.retryButton} onClick={() => router.refresh()}>
      Try again
    </button>
  );
}
