"""Camera lineup solve: camera pose and focal length from pins and perspective lines. numpy only (no bpy).

Conventions match Blender's camera: it looks down -Z with +X right and +Y up. Image points are pixels from the
bottom-left corner, y up. A camera is (R, C, f): R maps world directions onto the camera axes (its rows are the
camera's axes in world space), C is the camera centre in world space, f the focal length in pixels.

Constraints, all measured in pixels on the plate:
    pins        a world point and its pixel (2 equations each)
    axis lines  a segment drawn on the plate along a known world direction: the line must run through that
                direction's vanishing point (1 equation; rotation and focal only)
    edge lines  a segment drawn on the plate along an edge known by two world points: both points must project onto
                the line (2 equations)

The solve is Levenberg-Marquardt on all of them, started from several guesses and settled robustly first (far-off
ones pull less) so a stray pin can't drag it into the wrong camera; the ones still too far off from that are flagged
(Solution.far). The final fit is then a plain best fit over everything (or, with drop_outliers, over everything but
the flagged ones). The guesses: the current camera, vanishing points (two axes with 2+
lines each, as fSpy) and, with 3+ pins, the cameras most of the pins and lines agree with among exact solves from
three pins at a time (P3P, RANSAC-style). With fewer pins a sweep of cameras looking at the points from all round
stands in for those.

With focal free, the rough focal is a prior: a soft pull toward it (one standard deviation is half the range), so
the constraints decide the focal where they can and it stays near the rough focal where they can't, with the range a
hard limit either side. "Too far off" is OUTLIER_PX on a REFERENCE_WIDTH plate, scaled with the plate, or three times
the noise of the clicks that fit if that's more.

The lidar points are known to lidar_sigma (world units) as well as the clicks to the pixel: a pin's error is measured
against both, the 3D part as it shows on the plate at the pin's depth (f * lidar_sigma / depth px), so near pins
weigh less and must be further off to count as too far; distant ones are as before. Lidar edges likewise.
"""
from dataclasses import dataclass, field

import numpy as np

MIN_PINS = 4
MIN_POSITIONAL = 2         # pins + edge lines: what fixes where the camera is
SWEEP_DIRECTIONS = 48
SWEEP_ITERATIONS = 15
SWEEP_KEEP = 4
ITERATIONS = 200


@dataclass
class Solution:
    R: np.ndarray            # 3x3, world -> camera
    C: np.ndarray            # camera centre, world
    f: float                 # focal length, pixels
    residuals: np.ndarray    # (N, 2) pins: projected - observed, pixels
    rms: float               # pixels, over the pins and lines kept (each counts once)
    focal_sigma: float       # one standard deviation of f, pixels (0 when the focal is locked)
    at_limit: bool = False   # f ended at the edge of the focal range
    axis_errors: np.ndarray = field(default_factory=lambda: np.zeros(0))   # (M,) px off the vanishing point
    edge_errors: np.ndarray = field(default_factory=lambda: np.zeros(0))   # (K,) px, rms of both ends
    position_fixed: bool = False                               # lines only: the camera kept where it was
    focal_determined: bool = True                              # False: the focal is mostly the rough focal
    left_out: tuple = ()                                       # (pins, axis, edges) masks: left out of the fit
    outlier_px: float = 0.0                                    # how far off counted as too far, pixels
    far: tuple = ()                                            # (pins, axis, edges) masks: too far off the robust fit


def rotation(w):
    """Rotation matrix from a rotation vector (Rodrigues)."""
    theta = np.linalg.norm(w)
    if theta < 1e-12:
        return np.eye(3)
    k = w / theta
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(theta) * K + (1 - np.cos(theta)) * K @ K


def skew(v):
    return np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])


def project(R, C, f, principal, points):
    """(pixels (N, 2), depth (N,)) of world points; depth is the distance in front of the camera."""
    cam = (points - C) @ R.T
    depth = -cam[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        pixels = principal + f * cam[:, :2] / depth[:, None]
    return pixels, depth


def vanishing(R, f, principal, directions):
    """Homogeneous pixels (M, 3) where world directions vanish (w = 0: at infinity)."""
    c = directions @ R.T
    w = -c[:, 2]
    return np.c_[f * c[:, :2] + principal * w[:, None], w]


def axis_residuals(R, f, principal, A, B, D):
    """(M,) for segments A-B drawn along world directions D: how far B sits off the line from the segment's middle
    to the vanishing point (pixels, signed; half the length times the sine of the angle)."""
    v = vanishing(R, f, principal, D)
    m = (A + B) / 2
    u = v[:, :2] - m * v[:, 2:3]
    n = np.linalg.norm(u, axis=1)
    u = u / np.where(n > 1e-12, n, 1.0)[:, None]
    h = B - m
    return h[:, 0] * u[:, 1] - h[:, 1] * u[:, 0]


def edge_residuals(R, C, f, principal, A, B, P1, P2):
    """(2K,) signed distances (pixels) of the projected edge ends P1, P2 from the lines through A-B, or None if an
    end is behind the camera."""
    k = len(A)
    p, depth = project(R, C, f, principal, np.r_[P1, P2])
    if np.any(depth <= 0):
        return None
    t = B - A
    n = np.c_[-t[:, 1], t[:, 0]] / np.linalg.norm(t, axis=1)[:, None]
    return np.r_[np.sum((p[:k] - A) * n, axis=1), np.sum((p[k:] - A) * n, axis=1)]


def look_at(C, target, up=(0.0, 0.0, 1.0)):
    """R for a camera at C looking at target, rolled so world up is up."""
    forward = target - C
    forward = forward / np.linalg.norm(forward)
    up = np.asarray(up, float)
    if abs(forward @ up) > 0.99:
        up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    return np.array([right, np.cross(right, forward), -forward])


def rotvec(R):
    """Rotation vector (axis * angle) of a rotation matrix."""
    angle = np.arccos(np.clip((np.trace(R) - 1) / 2, -1.0, 1.0))
    if angle < 1e-12:
        return np.zeros(3)
    w = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    n = np.linalg.norm(w)
    if n < 1e-9:                                               # ~180 degrees: axis from the symmetric part
        axis = np.sqrt(np.maximum(np.diag(R) + 1, 0) / 2)
        return axis / np.linalg.norm(axis) * angle
    return w / n * angle


ANCHOR_WEIGHT = 0.1        # px per radian: holds what lines alone can't fix (e.g. turning about the verticals)
CLICK_ACCURACY = 1.0       # px: the error assumed of a click before the fit says, and the least for focal_sigma
NOISE_FLOOR = 0.01         # px: the least click error assumed from the fit
NOISE_DOF = 3              # with fewer spare equations than this the fit can't say: CLICK_ACCURACY
NOISE_ROUNDS = 4           # noise estimate / refine rounds
TIE = 0.01                 # robust costs this close (share) are a tie, e.g. mirror images
PRIOR_SIGMA = 0.5          # the focal prior's standard deviation, as a share of the focal range
FROM_DATA = 0.9            # focal sigma over this share of the prior's: the focal is mostly the rough focal
ROBUST_ROUNDS = 3          # reweighting rounds
REFERENCE_WIDTH = 1920     # px: plate width OUTLIER_PX is for (scaled with the plate)
OUTLIER_PX = 4.0           # px: off by more than this from the robust solve, a pin or line is too far off
OUTLIER_NOISE = 3.0        # or more than this many times the clicks' noise (robust estimate), if that's more
OUTLIER_SHARE = 1 / 3      # but never more than this share of them (beyond that it's the lineup, not outliers)
SUBSETS = 150              # most three-pin sets tried for the consensus guesses
CONSENSUS_KEEP = 4         # consensus guesses refined


class _Problem:
    """State is (R, C, f). The focal moves in log steps, clamped into focal +- focal_range, pulled toward focal by
    the prior (the last residual). With fixed_position the centre stays put and only rotation (and focal) move;
    anchor (a rotation) then weakly holds what the lines leave free. outlier_px is how far off is too far."""

    def __init__(self, points, pixels, principal, focal, focal_range, axis=None, edges=None, fixed_position=False,
                 anchor=None, outlier_px=OUTLIER_PX, lidar_sigma=0.0):
        self.points = points
        self.pixels = pixels
        self.principal = principal
        self.axis = axis                                       # (A, B, D) or None
        self.edges = edges                                     # (A, B, P1, P2) or None
        self.anchor = anchor
        self.focal = focal
        self.outlier_px = outlier_px
        self.lidar_sigma = lidar_sigma
        self.scales = (np.ones(len(points)), np.ones(0 if edges is None else len(edges[0])))
        self.lo, self.hi = focal - focal_range, focal + focal_range
        self.free_position = not fixed_position
        self.free_focal = focal_range > 0
        self.prior_sigma = PRIOR_SIGMA * focal_range / focal if self.free_focal else 0.0   # of log f
        self.noise = CLICK_ACCURACY                            # px: the clicks' error, weighing the prior
        self.nparams = 3 + 3 * self.free_position + self.free_focal
        world = np.r_[points, edges[2], edges[3]] if edges is not None else points
        spread = np.sqrt(np.mean(np.sum((world - world.mean(0)) ** 2, axis=1))) if len(world) else 1.0
        self.eps = np.array([1e-6] * 3 + [1e-6 * max(spread, 1e-9)] * 3 * self.free_position + [1e-6] * self.free_focal)
        self.set_weights(np.ones(len(points)), np.ones(0 if axis is None else len(axis[0])),
                         np.ones(0 if edges is None else len(edges[0])))

    def set_weights(self, pins, axis, edges):
        """Per pin / axis line / edge line weight on its residuals (0 leaves it out)."""
        self.weights = (np.asarray(pins, float), np.asarray(axis, float), np.asarray(edges, float))

    def set_scales(self, state):
        """How many clicks' worth each pin's and edge's error is, from the lidar's accuracy at its depth with the
        camera at state: held fixed between calls (were it to follow the camera, moving closer would look better)."""
        if self.lidar_sigma <= 0:
            return
        R, C, f = state

        def scale(depth):
            px = f * self.lidar_sigma / np.maximum(depth, 1e-9)
            return np.where(depth > 0, np.sqrt(1 + (px / CLICK_ACCURACY) ** 2), 1.0)

        pins = scale(project(R, C, f, self.principal, self.points)[1]) if len(self.points) else np.ones(0)
        edges = np.ones(0)
        if self.edges is not None:
            near = np.minimum(project(R, C, f, self.principal, self.edges[2])[1],
                              project(R, C, f, self.principal, self.edges[3])[1])
            edges = scale(near)
        self.scales = (pins, edges)

    def item_errors(self, state, raw=False):
        """(pins (N,), axis lines (M,), edge lines (K,)) errors in pixels, unweighted; unless raw, in clicks' worth
        (divided by the lidar scales)."""
        R, C, f = state
        pins = np.zeros(0)
        if len(self.points):
            pixels, depth = project(R, C, f, self.principal, self.points)
            pins = np.where(depth > 0, np.linalg.norm(pixels - self.pixels, axis=1), np.inf)
            if not raw:
                pins = pins / self.scales[0]
        axis = np.abs(axis_residuals(R, f, self.principal, *self.axis)) if self.axis is not None else np.zeros(0)
        edges = np.zeros(0)
        if self.edges is not None:
            k = len(self.edges[0])
            r = edge_residuals(R, C, f, self.principal, *self.edges)
            edges = np.full(k, np.inf) if r is None else np.sqrt((r[:k] ** 2 + r[k:] ** 2) / 2)
            if not raw:
                edges = edges / self.scales[1]
        return pins, axis, edges

    def residuals(self, state):
        R, C, f = state
        parts = [] if self.anchor is None else [ANCHOR_WEIGHT * rotvec(R @ self.anchor.T)]
        if len(self.points):
            pixels, depth = project(R, C, f, self.principal, self.points)
            if np.any(depth <= 0):
                return None
            parts.append(((pixels - self.pixels) * (self.weights[0] / self.scales[0])[:, None]).ravel())
        if self.axis is not None:
            parts.append(axis_residuals(R, f, self.principal, *self.axis) * self.weights[1])
        if self.edges is not None:
            r = edge_residuals(R, C, f, self.principal, *self.edges)
            if r is None:
                return None
            w = self.weights[2] / self.scales[1]
            parts.append(r * np.r_[w, w])
        if self.free_focal:
            parts.append([self.noise * np.log(f / self.focal) / self.prior_sigma])
        return np.concatenate(parts)

    def step(self, state, d, clamp=True):
        R, C, f = state
        if self.free_position:
            C = C + d[3:6]
        if self.free_focal:
            f = f * np.exp(d[-1])
            if clamp:
                f = min(max(f, self.lo), self.hi)
        return rotation(d[:3]) @ R, C, f

    def jacobian(self, state, r):
        J = np.empty((len(r), self.nparams))
        for i in range(self.nparams):
            d = np.zeros(self.nparams)
            d[i] = self.eps[i]
            ri = self.residuals(self.step(state, d, clamp=False))
            if ri is None:
                d[i] = -self.eps[i]
                ri = self.residuals(self.step(state, d, clamp=False))
                J[:, i] = (r - ri) / self.eps[i]
            else:
                J[:, i] = (ri - r) / self.eps[i]
        return J

    def refine(self, state, iterations):
        """(state, cost) after Levenberg-Marquardt from a valid state."""
        r = self.residuals(state)
        cost = r @ r
        lam = 1e-3
        for _ in range(iterations):
            J = self.jacobian(state, r)
            A = J.T @ J
            g = J.T @ r
            damping = np.diag(np.diag(A)) + 1e-12 * np.eye(self.nparams)
            accepted = False
            while lam < 1e12:
                try:
                    d = np.linalg.solve(A + lam * damping, -g)
                except np.linalg.LinAlgError:
                    lam *= 10
                    continue
                new = self.step(state, d)
                rn = self.residuals(new)
                if rn is not None and rn @ rn < cost:
                    accepted = True
                    break
                lam *= 10
            if not accepted:
                break
            done = cost - rn @ rn < 1e-12 * cost + 1e-15
            state, r, cost = new, rn, rn @ rn
            lam = max(lam / 10, 1e-12)
            if done:
                break
        return state, cost


def p3p(X, rays):
    """Cameras (R, C) that see world points X (3, 3) along unit rays (3, 3) in camera space: up to four (Grunert's
    solution, as reviewed by Haralick et al. 1994)."""
    a2, b2, c2 = (np.sum((X[i] - X[k]) ** 2) for i, k in ((1, 2), (0, 2), (0, 1)))
    if min(a2, b2, c2) < 1e-12:
        return []
    ca, cb, cg = rays[1] @ rays[2], rays[0] @ rays[2], rays[0] @ rays[1]
    p, q = (a2 - c2) / b2, (a2 + c2) / b2
    quartic = [(p - 1) ** 2 - 4 * c2 / b2 * ca ** 2,
               4 * (p * (1 - p) * cb - (1 - q) * ca * cg + 2 * c2 / b2 * ca ** 2 * cb),
               2 * (p ** 2 - 1 + 2 * p ** 2 * cb ** 2 + 2 * (b2 - c2) / b2 * ca ** 2 - 4 * q * ca * cb * cg
                    + 2 * (b2 - a2) / b2 * cg ** 2),
               4 * (-p * (1 + p) * cb + 2 * a2 / b2 * cg ** 2 * cb - (1 - q) * ca * cg),
               (1 + p) ** 2 - 4 * a2 / b2 * cg ** 2]
    if not np.all(np.isfinite(quartic)) or abs(quartic[0]) < 1e-15:
        return []
    cameras = []
    for v in np.roots(quartic):                                 # v = s3 / s1, u = s2 / s1 (distances along the rays)
        if abs(v.imag) > 1e-6 * max(1.0, abs(v)) or v.real <= 0:
            continue
        v = v.real
        den = 2 * (cg - v * ca)
        if abs(den) < 1e-12:
            continue
        u = ((p - 1) * v * v - 2 * p * cb * v + 1 + p) / den
        d = 1 + u * u - 2 * u * cg
        if u <= 0 or d <= 1e-12:
            continue
        s1 = np.sqrt(c2 / d)
        Q = rays * np.array([s1, u * s1, v * s1])[:, None]       # the points in camera space
        pm, qm = X.mean(0), Q.mean(0)
        U, _S, Vt = np.linalg.svd((X - pm).T @ (Q - qm))
        R = Vt.T @ np.diag([1.0, 1.0, np.linalg.det(Vt.T @ U.T)]) @ U.T
        cameras.append((R, pm - R.T @ qm))
    return cameras


def _consensus(problem, focal, focal_range):
    """(R, C, f) guesses: exact cameras through three pins at a time (at the rough focal and across the range when
    it's free), the ones the most pins and lines agree with (MSAC: each counts its squared error, capped)."""
    points, pixels, principal = problem.points, problem.pixels, problem.principal
    n = len(points)
    if n < 3:
        return []
    triples = np.array([(i, j, k) for i in range(n) for j in range(i + 1, n) for k in range(j + 1, n)])
    if len(triples) > SUBSETS:
        triples = triples[np.random.default_rng(0).choice(len(triples), SUBSETS, replace=False)]
    focals = [focal] + ([focal + focal_range * s for s in (-1.0, -0.5, 0.5, 1.0)] if focal_range > 0 else [])
    cap = (2 * problem.outlier_px) ** 2
    scored = []
    for f in focals:
        rays = np.c_[(pixels - principal) / f, -np.ones(n)]
        rays /= np.linalg.norm(rays, axis=1)[:, None]
        for t in triples:
            for R, C in p3p(points[t], rays[t]):
                errors = np.concatenate(problem.item_errors((R, C, f)))
                scored.append((float(np.sum(np.minimum(errors ** 2, cap))), (R, C, f)))
    scored.sort(key=lambda s: s[0])
    guesses = []
    spread = np.sqrt(np.mean(np.sum((points - points.mean(0)) ** 2, axis=1)))
    for _score, (R, C, f) in scored:                            # the best few, not near repeats of each other
        if all(np.linalg.norm(C - g[1]) > 0.01 * spread for g in guesses):
            guesses.append((R, C, f))
            if len(guesses) == CONSENSUS_KEEP:
                break
    return guesses


def _sweep(points, pixels, focal, principal):
    """(R, C) guesses: cameras all round the points, looking at them, rolled and backed off to fit the plate."""
    if len(points) < 2:
        return []
    centre = points.mean(0)
    spread3 = np.sqrt(np.mean(np.sum((points - centre) ** 2, axis=1)))
    q = pixels - pixels.mean(0)
    spread2 = np.sqrt(np.mean(np.sum(q ** 2, axis=1)))
    if spread3 < 1e-12 or spread2 < 1e-12:
        return []
    distance = focal * spread3 / spread2
    n = SWEEP_DIRECTIONS
    i = np.arange(n) + 0.5
    z = 1 - 2 * i / n
    a = np.pi * (1 + 5 ** 0.5) * i
    dirs = np.c_[np.sqrt(1 - z * z) * np.cos(a), np.sqrt(1 - z * z) * np.sin(a), z]
    guesses = []
    for direction in dirs:
        C = centre + direction * distance
        R = look_at(C, centre)
        projected, depth = project(R, C, focal, principal, points)
        if np.any(depth <= 0):
            continue
        p = projected - projected.mean(0)
        theta = np.arctan2(np.sum(p[:, 0] * q[:, 1] - p[:, 1] * q[:, 0]), np.sum(p * q))
        c, s = np.cos(theta), np.sin(theta)
        guesses.append((np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]) @ R, C))
    return guesses


def _position(R, f, principal, points, pixels, edges):
    """Least-squares camera centre for a known rotation and focal, from pins and edge lines (linear), or None."""
    rows, rhs = [], []
    for X, x in zip(points, pixels):
        Q = skew(np.array([(x[0] - principal[0]) / f, (x[1] - principal[1]) / f, -1.0])) @ R
        rows.append(Q)
        rhs.append(Q @ X)
    if edges is not None:
        A, B, P1, P2 = edges
        for k in range(len(A)):
            l = np.cross([*A[k], 1.0], [*B[k], 1.0])
            g = np.array([l[0] * f, l[1] * f, -(l[0] * principal[0] + l[1] * principal[1] + l[2])]) @ R
            for P in (P1[k], P2[k]):
                rows.append(g[None])
                rhs.append(np.array([g @ P]))
    if not rows:
        return None
    M, b = np.vstack(rows), np.concatenate(rhs)
    if np.linalg.matrix_rank(M, tol=1e-9 * max(np.abs(M).max(), 1e-12)) < 3:
        return None
    return np.linalg.lstsq(M, b, rcond=None)[0]


def _axis_key(d):
    d = d / np.linalg.norm(d)
    i = np.flatnonzero(np.abs(d) > 1e-9)[0]
    return tuple(np.round(d * np.sign(d[i]), 6))               # X and -X are the same axis


def _vanishing_guesses(points, pixels, focal, principal, focal_range, axis, edges, centre=None):
    """(R, C, f) guesses from the vanishing points of two world directions with 2+ lines each (as fSpy): focal from
    the two (if free), rotation from their directions (four sign choices), position from pins and edge lines (or
    centre, when given)."""
    if axis is None:
        return []
    A, B, D = axis
    groups = {}
    for i, d in enumerate(D):
        groups.setdefault(_axis_key(d), []).append(i)
    vps = []
    for key, idx in groups.items():
        if len(idx) < 2:
            continue
        lines = np.cross(np.c_[A[idx], np.ones(len(idx))], np.c_[B[idx], np.ones(len(idx))])
        lines /= np.linalg.norm(lines[:, :2], axis=1)[:, None]
        vps.append((np.array(key), np.linalg.svd(lines)[2][-1]))
    if len(vps) < 2:
        return []
    (d1, v1), (d2, v2) = sorted(((a, b) for a in vps for b in vps if a is not b),
                                key=lambda ab: abs(ab[0][0] @ ab[1][0]))[0]   # the most nearly square pair

    f = focal
    if focal_range > 0 and abs(v1[2]) > 1e-9 and abs(v2[2]) > 1e-9:
        a = v1[:2] / v1[2] - principal
        b = v2[:2] / v2[2] - principal
        if -(a @ b) > 0:
            f = float(np.clip(np.sqrt(-(a @ b)), focal - focal_range, focal + focal_range))

    def camera_direction(v):
        c = np.array([(v[0] - principal[0] * v[2]) / f, (v[1] - principal[1] * v[2]) / f, -v[2]])
        return c / np.linalg.norm(c)

    c1, c2 = camera_direction(v1), camera_direction(v2)
    guesses = []
    for s1 in (1.0, -1.0):
        for s2 in (1.0, -1.0):
            a, b = s1 * c1, s2 * c2
            M = np.outer(a, d1) + np.outer(b, d2) + np.outer(np.cross(a, b), np.cross(d1, d2))
            U, _S, Vt = np.linalg.svd(M)
            R = U @ np.diag([1.0, 1.0, np.linalg.det(U @ Vt)]) @ Vt
            C = centre if centre is not None else _position(R, f, principal, points, pixels, edges)
            if C is not None:
                guesses.append((R, C, f))
    return guesses


def _robust(problem, state):
    """(state, robust cost) after reweighted refines: far-off pins and lines pull less and less (Cauchy, past half
    the outlier distance), so the ones that agree decide the camera."""
    scale = problem.outlier_px / 2
    for _ in range(ROBUST_ROUNDS):
        problem.set_scales(state)
        problem.set_weights(*(1.0 / np.sqrt(1.0 + (e / scale) ** 2) for e in problem.item_errors(state)))
        state, _cost = problem.refine(state, ITERATIONS)
    problem.set_scales(state)
    return state, sum(float(np.sum(np.log1p((e / scale) ** 2))) for e in problem.item_errors(state))


def _enough(problem, keep):
    pins, axis, edges = (int(np.sum(k)) for k in keep)
    equations = 2 * pins + axis + 2 * edges
    if problem.free_position:
        return pins + edges >= MIN_POSITIONAL and equations > problem.nparams
    return equations >= 2


def _limit(problem, errors):
    """How far off is too far: the outlier distance, or OUTLIER_NOISE times the noise of the clicks if that's more
    (from the median error, so the far-off ones don't count)."""
    every = np.concatenate(errors)
    every = every[np.isfinite(every)]
    noise = 1.4826 * float(np.median(every)) if len(every) else 0.0
    return max(problem.outlier_px, OUTLIER_NOISE * noise)


def _settle(problem, starts, drop_outliers=False):
    """(best state, kept masks (pins, axis, edges), far masks, outlier limit px): robust refine from every start, the
    best by robust cost (ties, mirror images, go to the camera the right way up, then the one nearest the anchor),
    whatever is too far off from that flagged, then a plain best fit over everything (with drop_outliers, everything
    but the flagged)."""
    results = [_robust(problem, s) for s in starts]
    least = min(cost for _state, cost in results)

    def preference(state):
        R = state[0]
        turn = 0.0 if problem.anchor is None else float(np.linalg.norm(rotvec(R @ problem.anchor.T)))
        return round(float(R[1, 2]), 2), -turn

    best, _cost = max((sc for sc in results if sc[1] <= least * (1 + TIE) + 1e-3), key=lambda sc: preference(sc[0]))
    problem.set_scales(best)
    errors = problem.item_errors(best)
    limit = _limit(problem, errors)
    far = tuple(e > limit for e in errors)
    total = sum(len(e) for e in errors)
    off = sum(int(np.sum(e > problem.outlier_px)) for e in errors)    # many off by the plain distance: the lineup
    if off > max(1, int(total * OUTLIER_SHARE)) or not _enough(problem, tuple(~x for x in far)):
        far = tuple(np.zeros(len(e), bool) for e in errors)
    keep = tuple(~x if drop_outliers else np.ones(len(x), bool) for x in far)
    problem.set_weights(*(k.astype(float) for k in keep))
    for _ in range(NOISE_ROUNDS):                              # the prior's pull shows as misfit: settle the two
        problem.set_scales(best)
        problem.noise = _noise(problem, best)
        best, _cost = problem.refine(best, ITERATIONS)
    return best, keep, far, limit


def _noise(problem, state):
    """The clicks' error (px) from how well what's kept fits, at most CLICK_ACCURACY (a misfit from the focal range
    being too tight isn't click error), and CLICK_ACCURACY when there's too little to tell: the tighter the clicks
    fit, the less the focal prior counts against them."""
    r = problem.residuals(state)
    if problem.free_focal:
        r = r[:-1]
    weights = np.concatenate([np.repeat(problem.weights[0], 2), problem.weights[1], np.repeat(problem.weights[2], 2)])
    dof = int(np.count_nonzero(weights)) - problem.nparams
    if dof < NOISE_DOF:
        return CLICK_ACCURACY
    data = r[len(r) - len(weights):]                           # without the anchor
    return float(np.clip(np.sqrt(data @ data / dof), NOISE_FLOOR, CLICK_ACCURACY))


def _lines(lines, width):
    """Lists of tuples -> stacked arrays, or None when empty."""
    if not lines:
        return None
    return tuple(np.asarray([line[i] for line in lines], float).reshape(len(lines), -1) for i in range(width))


def solve(points, pixels, focal, principal, focal_range=0.0, initial=None, axis_lines=None, edge_lines=None,
          plate_width=None, drop_outliers=False, lidar_sigma=0.0):
    """Solution for the camera that fits the pins and lines.

    points, pixels: pins, world points and their plate pixels. focal is the rough focal length in pixels; the solved
    focal stays within focal +- focal_range (pixels; 0 locks it), pulled toward focal where the pins and lines
    don't decide it. principal is the optical centre in pixels. plate_width (pixels) scales how far off a pin or
    line must be to be too far off (OUTLIER_PX at REFERENCE_WIDTH): those are flagged in Solution.far and, with
    drop_outliers, left out of the fit; otherwise the fit is a best fit over all of them. lidar_sigma (world units)
    is how well the lidar points are known: near pins and edges weigh less by it and must be further off to be
    too far.
    initial is an optional (R, C) guess, such as the camera as it stands.
    axis_lines: [(a, b, direction)]: plate segment a-b (pixels) drawn along a world direction.
    edge_lines: [(a, b, p1, p2)]: plate segment a-b drawn along the world edge through p1 and p2.

    With fewer than MIN_POSITIONAL pins and edges nothing fixes where the camera is: if there are axis lines (2+)
    and an initial camera, its rotation and focal are solved from the lines and it stays where it is (as fSpy
    without an origin); Solution.position_fixed says so.
    """
    points = np.asarray(points, float).reshape(-1, 3)
    pixels = np.asarray(pixels, float).reshape(-1, 2)
    principal = np.asarray(principal, float)
    if len(points) != len(pixels):
        raise ValueError("points and pixels differ in length")
    axis = _lines(axis_lines, 3)
    edges = _lines(edge_lines, 4)
    n, m, k = len(points), len(axis_lines or ()), len(edge_lines or ())
    focal_range = min(max(focal_range, 0.0), 0.95 * focal)     # keep the focal positive
    outlier_px = OUTLIER_PX * (plate_width / REFERENCE_WIDTH if plate_width else 1.0)
    if n + k < MIN_POSITIONAL:
        if m < 2 or initial is None:
            raise ValueError(f"Not enough to solve: {n} pins, {k} lidar edges, {m} axis lines. Draw 2+ lines to set "
                             f"rotation and focal; add 2+ pins to also place the camera")
        return _solve_rotation(points, pixels, focal, principal, focal_range, initial, axis, edges, outlier_px,
                               drop_outliers, lidar_sigma)
    nparams = 7 if focal_range > 0 else 6
    equations = 2 * n + m + 2 * k
    if equations <= nparams:
        raise ValueError(f"Not enough to solve: {n} pins, {k} lidar edges, {m} axis lines. Need {nparams + 1}+ in "
                         f"all, counting pins and edges twice")
    problem = _Problem(points, pixels, principal, focal, focal_range, axis, edges, outlier_px=outlier_px,
                       lidar_sigma=lidar_sigma)

    def valid(state):
        return problem.residuals(state) is not None

    starts = [(initial[0], initial[1], focal)] if initial is not None else []
    starts += _vanishing_guesses(points, pixels, focal, principal, focal_range, axis, edges)
    starts += _consensus(problem, focal, focal_range)
    starts = [s for s in starts if valid(s)]
    if len(points) < 3 or not starts:
        # Too few pins for the consensus: a sweep looking at the pins and the edges' ends (taking each end at its
        # end of the drawn segment).
        pose_only = _Problem(points, pixels, principal, focal, 0.0, axis, edges)
        sweep_points, sweep_pixels = points, pixels
        if edges is not None:
            sweep_points = np.r_[points, edges[2], edges[3]]
            sweep_pixels = np.r_[pixels, edges[0], edges[1]]
        coarse = sorted((pose_only.refine((R, C, focal), SWEEP_ITERATIONS)
                         for R, C in _sweep(sweep_points, sweep_pixels, focal, principal)
                         if valid((R, C, focal))), key=lambda sc: sc[1])
        starts += [state for state, _cost in coarse[:SWEEP_KEEP]]
    if not starts:
        raise ValueError("No camera sees every pin and edge in front of it; check them")
    best, keep, far, limit = _settle(problem, starts, drop_outliers)
    return _solution(problem, best, keep, far, limit, points, pixels, focal, principal, focal_range)


def _solve_rotation(points, pixels, focal, principal, focal_range, initial, axis, edges, outlier_px,
                    drop_outliers=False, lidar_sigma=0.0):
    """Rotation and focal from the lines, the camera kept at initial's centre; the rotation the lines leave free is
    held near initial's."""
    R0, C0 = (np.asarray(x, float) for x in initial)
    problem = _Problem(points, pixels, principal, focal, focal_range, axis, edges, fixed_position=True, anchor=R0,
                       outlier_px=outlier_px, lidar_sigma=lidar_sigma)
    starts = [(R0, C0, focal)]
    starts += [(R, C0, f) for R, _C, f in _vanishing_guesses(points, pixels, focal, principal, focal_range, axis,
                                                             edges, centre=C0)]
    starts = [s for s in starts if problem.residuals(s) is not None]
    if not starts:
        raise ValueError("The camera doesn't see every pin and edge in front of it; check them")
    best, keep, far, limit = _settle(problem, starts, drop_outliers)
    sol = _solution(problem, best, keep, far, limit, points, pixels, focal, principal, focal_range)
    sol.position_fixed = True
    return sol


def _solution(problem, best, keep, far, limit, points, pixels, focal, principal, focal_range):
    R, C, f = best
    f = float(f)
    projected, _depth = project(R, C, f, principal, points)
    residuals = projected - pixels
    pin_errors, axis_errors, edge_errors = problem.item_errors(best, raw=True)
    kept = np.r_[pin_errors[keep[0]], axis_errors[keep[1]], edge_errors[keep[2]]]
    rms = float(np.sqrt(np.mean(kept ** 2))) if len(kept) else 0.0
    focal_sigma = 0.0
    at_limit = False
    determined = True
    if problem.free_focal:
        at_limit = abs(f - focal) > 0.98 * focal_range
        r = problem.residuals(best)
        J = problem.jacobian(best, r)
        J, r = J[:-1], r[:-1]                                  # the constraints, without the prior
        dof = int(np.count_nonzero(np.any(J != 0, axis=1))) - problem.nparams     # left-out rows don't count
        variance = max((r @ r) / dof if dof > 0 else 0.0, CLICK_ACCURACY ** 2)
        information = J.T @ J / variance
        information[-1, -1] += 1 / problem.prior_sigma ** 2
        sigma = np.sqrt(max(np.linalg.pinv(information)[-1, -1], 0.0))          # of log f, prior included
        focal_sigma = float(f * sigma)
        # Little better known than from the prior alone (e.g. verticals on a level camera and one horizontal
        # direction): the focal is mostly the rough focal.
        determined = sigma < FROM_DATA * problem.prior_sigma
    return Solution(R, C, f, residuals, rms, focal_sigma, at_limit, axis_errors, edge_errors,
                    focal_determined=determined, left_out=tuple(~k for k in keep), outlier_px=limit, far=far)
