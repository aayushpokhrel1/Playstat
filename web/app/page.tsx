import Link from "next/link";
import { getPlayers, getTeams } from "./lib/api";

export default async function Home() {
  const [teams, players] = await Promise.all([getTeams(), getPlayers()]);

  const playersByTeam = new Map<number, typeof players>();
  for (const player of players) {
    if (player.team_id === null) continue;
    const list = playersByTeam.get(player.team_id) ?? [];
    list.push(player);
    playersByTeam.set(player.team_id, list);
  }

  return (
    <main style={{ padding: "2rem", maxWidth: 900, margin: "0 auto" }}>
      <h1>Playstat</h1>
      <p style={{ color: "#666" }}>{teams.length} teams, {players.length} players</p>
      <p style={{ marginTop: "0.5rem" }}>
        <Link href="/edges">View tonight&apos;s edges &rarr;</Link>
      </p>
      <p style={{ marginTop: "0.5rem" }}>
        <Link href="/clv">View model performance &rarr;</Link>
      </p>

      {teams.map((team) => (
        <section key={team.team_id} style={{ marginTop: "1.5rem" }}>
          <h2 style={{ fontSize: "1.1rem", marginBottom: "0.5rem" }}>{team.name}</h2>
          <ul style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem 1.5rem", listStyle: "none", padding: 0 }}>
            {(playersByTeam.get(team.team_id) ?? []).map((player) => (
              <li key={player.player_id}>
                <Link href={`/players/${player.player_id}`}>
                  {player.name} {player.position ? `(${player.position})` : ""}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </main>
  );
}
