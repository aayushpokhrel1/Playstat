import styles from "./login.module.css";

export const metadata = { title: "Log in · Playstat" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;

  return (
    <main className={styles.root}>
      <div className={styles.panel}>
        <h1 className={styles.wordmark}>Playstat</h1>
        <p className={styles.tagline}>model edges, calibrated.</p>

        <form className={styles.form} method="POST" action="/api/login">
          {error && (
            <p className={styles.error} role="alert">
              Wrong username or password.
            </p>
          )}
          <div className={styles.field}>
            <label className={styles.label} htmlFor="username">
              Username
            </label>
            <input
              className={styles.input}
              id="username"
              name="username"
              type="text"
              autoComplete="username"
              autoFocus
              required
            />
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="password">
              Password
            </label>
            <input
              className={styles.input}
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </div>
          <button className={styles.submit} type="submit">
            Log in
          </button>
        </form>
      </div>
    </main>
  );
}
