# Product

## Register

product

## Platform

web

## Users

Primarily Aayush himself — a solo bettor/analyst who opens Playstat before a night of games to decide where to act. He's fluent in the numbers (edges, calibration, implied probability) and doesn't need things explained. Secondary, longer-term: a friend or two, and possibly a wider audience if this opens up beyond personal use — so the UI shouldn't lean so far into insider shorthand that a newcomer can't eventually follow it.

## Product Purpose

A dashboard that turns the model's disagreement with the sportsbook market into something you can act on fast: predicted stat lines against sportsbook lines, where the edge is, and a suggested parlay for a target payout. It replaces querying the database or reading raw prediction output by hand.

## Positioning

The one screen where a model prediction becomes a trusted, actionable edge — not a stats browser, not a raw data dump.

## Brand Personality

Terminal / analyst-native crossed with sportsbook-modern, expressed with quiet, editorial restraint: Bloomberg-style density and numerical credibility, sportsbook-grade confidence and glanceability, but without the sportsbook's gradient-and-hype visual language. Precision over decoration.

## Anti-references

The generic SaaS-dashboard-in-a-box look (card grids, pastel KPI tiles, rounded-everything, a stray gradient accent) — it undersells how much rigor is behind the numbers. Also avoid actual sportsbook chrome (odds-app skins, promo banners, gamified color) — the tone is analyst, not marketing.

## Design Principles

Density over decoration — the interface earns its data density instead of padding it out with card chrome.

Numbers earn trust before they earn attention — a prediction reads with its calibration context, not just a bold colored figure.

Speed and depth share a screen — the same view supports a five-second scan for tonight's edges and a longer dig into a player's trend, without forcing a choice between them.

Confidence without hype — visual polish signals credibility, not promotion.

Built to open up — solo-use today, but legible enough that a friend or a future user could read it without a walkthrough.

## Accessibility & Inclusion

No formal compliance target while this stays personal-use, but body text should still clear AA contrast (4.5:1) and the UI should support both light and dark (`prefers-color-scheme`), leaning dark given the terminal-native personality — since it's expected to open up beyond solo use later.
