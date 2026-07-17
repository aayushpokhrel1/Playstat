"""Shared moment-based reconstruction of discrete predictive distributions.

model_predictions stores only (predicted_mean, predicted_std) — no schema change
(README §11's discrete-distribution follow-up keeps that storage). For the 13 MLB
count stats those two moments now describe a *discrete* law, reconstructed here so
edges.py, calibration.py, backtest.py and eval_discrete.py all agree on the math
(no duplicated CDF logic).

Reconstruction rule (single source of truth):
    var = std**2
    var <= mean (within EPS)  -> Poisson(mean)          (equidispersed)
    var >  mean               -> NB2 with r = mean^2 / (var - mean)   (overdispersed)

NB2 is the negative binomial with Var = mean + mean^2/r. scipy's nbinom(n, p) has
mean = n(1-p)/p; setting n = r and p = r/(r+mean) yields exactly that mean/variance.
"""

import math

import numpy as np
from scipy.stats import nbinom, norm, poisson

# Floor on the reconstructed mean (a nonnegative count law needs mean > 0) and the
# slack allowed before std**2 - mean counts as genuine overdispersion rather than
# floating-point noise or mild underdispersion (which we round to Poisson).
EPS = 1e-6


def discrete_dist(mean, std):
    """Frozen scipy distribution reconstructed from (mean, std) for a count stat.

    Returns a Poisson when variance <= mean (equi-/under-dispersed, clamped to
    Poisson), else an NB2 negative binomial. Callers use .cdf/.pmf/.ppf on it.
    """
    mean = max(float(mean), EPS)
    var = float(std) ** 2
    if var <= mean + EPS:
        return poisson(mean)
    r = mean * mean / (var - mean)
    p = r / (r + mean)
    return nbinom(r, p)


def prob_over_discrete(mean, std, line):
    """P(X > line) for the reconstructed count law.

    Prop lines are half-integers (0.5, 1.5, ...), so "over" means X >= ceil(line),
    i.e. P(X > line) = 1 - CDF(floor(line)). floor keeps this exact for the rare
    integer line too (over an integer line excludes the push value)."""
    dist = discrete_dist(mean, std)
    return float(1.0 - dist.cdf(math.floor(line)))


def prob_over_gaussian(mean, std, line):
    """P(X > line) under a Gaussian — the NBA path, unchanged from edges.py."""
    return float(1.0 - norm.cdf(line, loc=mean, scale=std))


def prob_over(mean, std, line, family):
    """Dispatch P(over line) by stat family: 'discrete' (MLB) or 'gaussian' (NBA)."""
    if family == "discrete":
        return prob_over_discrete(mean, std, line)
    return prob_over_gaussian(mean, std, line)


def _split_params(mean, std):
    """Vectorized reconstruction parameters: (poisson_mask, mu, r, p).

    Same rule as discrete_dist applied over arrays — rows with var <= mean are
    Poisson(mu); the rest NB2 with r = mu^2/(var - mu), p = r/(r + mu)."""
    mu = np.maximum(np.atleast_1d(np.asarray(mean, dtype=float)), EPS)
    var = np.atleast_1d(np.asarray(std, dtype=float)) ** 2
    pois = var <= mu + EPS
    denom = np.where(pois, 1.0, var - mu)  # placeholder 1.0 where unused
    r = mu * mu / denom
    p = r / (r + mu)
    return pois, mu, r, p


def cdf_array(mean, std, k):
    """Vectorized CDF(k) of the reconstructed law — identical math to
    discrete_dist, without per-row frozen-distribution overhead (the per-row
    loop was ~100x slower, which mattered at backtest scale: 25k test rows)."""
    pois, mu, r, p = _split_params(mean, std)
    k = np.asarray(k, dtype=float)
    return np.where(pois, poisson.cdf(k, mu), nbinom.cdf(k, np.where(pois, 1.0, r), p))


def ppf_array(mean, std, q):
    """Vectorized q-th percentile (smallest integer k with CDF(k) >= q)."""
    pois, mu, r, p = _split_params(mean, std)
    return np.where(pois, poisson.ppf(q, mu), nbinom.ppf(q, np.where(pois, 1.0, r), p))


def randomized_pit(mean, std, x, seed=0):
    """Randomized PIT values for discrete observations x.

        u = F(x-1) + V * (F(x) - F(x-1)),  V ~ U(0,1)

    Spreads each integer's probability mass across its CDF interval so that, when
    the predictive law is correct, u is exactly Uniform(0,1) despite the atoms.
    Vectorized; one V is drawn per element."""
    x = np.atleast_1d(np.asarray(x, dtype=float))
    hi = cdf_array(mean, std, x)
    lo = cdf_array(mean, std, x - 1)
    v = np.random.default_rng(seed).uniform(size=x.shape[0])
    return lo + v * (hi - lo)


def discrete_ppf(mean, std, q):
    """Smallest integer k with CDF(k) >= q for the reconstructed law (the q-th
    percentile of a lumpy count). Used for discrete coverage checks."""
    return float(discrete_dist(mean, std).ppf(q))


def pmf_list(mean, std, cover=0.999, k_cap=60):
    """The full predictive PMF as [(k, P(X=k)), ...] for k = 0..k_max, where
    k_max = min(discrete_ppf(mean, std, cover), k_cap) — enough bars to cover
    `cover` of the mass without an unbounded tail for a display-sized chart.

    Used by the /edge-distributions endpoint (README §14.5) so the drawn PMF
    is exactly the same law compute_edges/prob_over_discrete use — no
    reimplementation of the reconstruction. Guards degenerate tiny-mean cases
    (k_max could come back 0) so callers always get at least k=0..1.
    """
    k_max = int(discrete_ppf(mean, std, cover))
    k_max = max(1, min(k_max, k_cap))
    dist = discrete_dist(mean, std)
    return [(k, float(dist.pmf(k))) for k in range(0, k_max + 1)]
