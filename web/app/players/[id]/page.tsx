import { notFound } from "next/navigation";
import Link from "next/link";
import { getPlayer, getPlayerStats } from "../../lib/api";

export default async function PlayerPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  if (!/^\d+$/.test(id)) {
    notFound();
  }
  const playerId = Number(id);

  const player = await getPlayer(playerId);
  if (player === null) {
    notFound();
  }

  const stats = await getPlayerStats(playerId);

  return (
    <main style={{ padding: "2rem", maxWidth: 900, margin: "0 auto" }}>
      <Link href="/">&larr; Back</Link>
      <h1>{player.name}</h1>
      <p style={{ color: "#666" }}>{player.position}</p>

      <section style={{ marginTop: "1.5rem" }}>
        <h2 style={{ fontSize: "1.1rem" }}>Recent games</h2>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
              <th>Date</th>
              <th>PTS</th>
              <th>REB</th>
              <th>AST</th>
              <th>MIN</th>
            </tr>
          </thead>
          <tbody>
            {stats.map((game) => (
              <tr key={game.game_id} style={{ borderBottom: "1px solid #eee" }}>
                <td>{game.date}</td>
                <td>{game.points ?? "-"}</td>
                <td>{game.rebounds ?? "-"}</td>
                <td>{game.assists ?? "-"}</td>
                <td>{game.minutes ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
