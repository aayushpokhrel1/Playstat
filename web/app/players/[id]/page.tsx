import { notFound } from "next/navigation";
import Link from "next/link";
import { getPlayer, getPlayerPredictions, getPlayerStats } from "../../lib/api";

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

  const [stats, predictions] = await Promise.all([
    getPlayerStats(playerId),
    getPlayerPredictions(playerId),
  ]);

  const predictionsByStat = new Map<string, typeof predictions>();
  for (const p of predictions) {
    const list = predictionsByStat.get(p.stat_type) ?? [];
    list.push(p);
    predictionsByStat.set(p.stat_type, list);
  }

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

      {[...predictionsByStat.entries()].map(([statType, preds]) => (
        <section key={statType} style={{ marginTop: "1.5rem" }}>
          <h2 style={{ fontSize: "1.1rem", textTransform: "capitalize" }}>
            {statType} — predicted vs actual
          </h2>
          <table style={{ borderCollapse: "collapse", width: "100%" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
                <th>Date</th>
                <th>Predicted</th>
                <th>Actual</th>
              </tr>
            </thead>
            <tbody>
              {preds.map((p) => (
                <tr key={p.game_id} style={{ borderBottom: "1px solid #eee" }}>
                  <td>{p.date}</td>
                  <td>
                    {p.predicted_mean.toFixed(1)} &plusmn; {p.predicted_std.toFixed(1)}
                  </td>
                  <td>{p.actual ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </main>
  );
}
