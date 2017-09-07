# -*- coding: utf-8 -*-
import abc
from contextlib import contextmanager
from copy import deepcopy as copy
import functools
import heapq
import itertools
from math import sqrt, isinf
from operator import itemgetter

import holoviews as hv
import numpy as np
from scipy import interpolate, optimize, special
import sortedcontainers

class BaseLearner(metaclass=abc.ABCMeta):
    """Base class for algorithms for learning a function 'f: X → Y'

    Attributes
    ----------
    function : callable: X → Y
        The function to learn.
    data : dict: X → Y
        'function' evaluated at certain points.
        The values can be 'None', which indicates that the point
        will be evaluated, but that we do not have the result yet.

    Subclasses may define a 'plot' method that takes no parameters
    and returns a holoviews plot.
    """
    def __init__(self, function):
        self.data = {}
        self.function = function

    def add_data(self, xvalues, yvalues):
        """Add data to the learner.

        Parameters
        ----------
        xvalues : value from the function domain, or iterable of such
            Values from the domain of the learned function.
        yvalues : value from the function image, or iterable of such
            Values from the range of the learned function, or None.
            If 'None', then it indicates that the value has not yet
            been computed.
        """
        try:
            for x, y in zip(xvalues, yvalues):
                self.add_point(x, y)
        except TypeError:
            self.add_point(xvalues, yvalues)

    @abc.abstractmethod
    def add_point(self, x, y):
        """Add a single datapoint to the learner."""
        pass

    @abc.abstractmethod
    def remove_unfinished(self):
        """Remove uncomputed data from the learner."""
        pass

    @abc.abstractmethod
    def loss(self, real=True):
        """Return the loss for the current state of the learner.

        Parameters
        ----------
        real : bool, default: True
            If False, return the "expected" loss, i.e. the
            loss including the as-yet unevaluated points
            (possibly by interpolation).
        """

    @abc.abstractmethod
    def choose_points(self, n, add_data=True):
        """Choose the next 'n' points to evaluate.

        Parameters
        ----------
        n : int
            The number of points to choose.
        add_data : bool, default: True
            If True, add the chosen points to this
            learner's 'data' with 'None' for the 'y'
            values. Set this to False if you do not
            want to modify the state of the learner.
        """
        pass

    def __getstate__(self):
        return copy(self.__dict__)

    def __setstate__(self, state):
        self.__dict__ = state

class AverageLearner(BaseLearner):
    def __init__(self, function, atol=None, rtol=None):
        """A naive implementation of adaptive computing of averages.

        The learned function must depend on an integer input variable that
        represents the source of randomness.

        Parameters:
        -----------
        atol : float
            Desired absolute tolerance
        rtol : float
            Desired relative tolerance
        """
        super().__init__(function)

        if atol is None and rtol is None:
            raise Exception('At least one of `atol` and `rtol` should be set.')
        if atol is None:
            atol = np.inf
        if rtol is None:
            rtol = np.inf

        self.function = function
        self.atol = atol
        self.rtol = rtol
        self.n = 0
        self.n_requested = 0
        self.sum_f = 0
        self.sum_f_sq = 0

    def choose_points(self, n=10, add_data=True):
        points = list(range(self.n_requested, self.n_requested + n))
        loss_improvements = [None] * n
        if add_data:
            self.add_data(points, itertools.repeat(None))
        return points, loss_improvements

    def add_point(self, n, value):
        self.data[n] = value
        if value is None:
            self.n_requested += 1
            return
        else:
            self.n += 1
            self.sum_f += value
            self.sum_f_sq += value**2

    @property
    def mean(self):
        return self.sum_f / self.n

    @property
    def std(self):
        n = self.n
        if n < 2:
            return np.inf
        return sqrt((self.sum_f_sq - n * self.mean**2) / (n - 1))

    def loss(self, real=True):
        n = self.n
        if n < 2:
            return np.inf
        standard_error = self.std / sqrt(n if real else self.n_requested)
        return max(standard_error / self.atol,
                   standard_error / abs(self.mean) / self.rtol)

    def remove_unfinished(self):
        """Remove uncomputed data from the learner."""
        pass

    def plot(self):
        vals = [v for v in self.data.values() if v is not None]
        if not vals:
            return hv.Histogram([[], []])
        num_bins = int(max(5, sqrt(self.n)))
        vals = hv.Points(vals)
        return hv.operation.histogram(vals, num_bins=num_bins, dimension=1)


class Learner1D(BaseLearner):
    """Learns and predicts a function 'f:ℝ → ℝ'.

    Parameters
    ----------
    function : callable
        The function to learn. Must take a single real parameter and
        return a real number.
    bounds : pair of reals
        The bounds of the interval on which to learn 'function'.
    """

    def __init__(self, function, bounds):
        super().__init__(function)

        # A dict storing the loss function for each interval x_n.
        self.losses = {}
        self.losses_combined = {}

        self.data = sortedcontainers.SortedDict()
        self.data_interp = {}

        # A dict {x_n: [x_{n-1}, x_{n+1}]} for quick checking of local
        # properties.
        self.neighbors = sortedcontainers.SortedDict()
        self.neighbors_combined = sortedcontainers.SortedDict()

        # Bounding box [[minx, maxx], [miny, maxy]].
        self._bbox = [list(bounds), [np.inf, -np.inf]]

        # Data scale (maxx - minx), (maxy - miny)
        self._scale = [bounds[1] - bounds[0], 0]
        self._oldscale = copy(self._scale)

        self.bounds = list(bounds)

    @property
    def data_combined(self):
        return {**self.data, **self.data_interp}

    def interval_loss(self, x_left, x_right, data):
        """Calculate loss in the interval x_left, x_right.

        Currently returns the rescaled length of the interval. If one of the
        y-values is missing, returns 0 (so the intervals with missing data are
        never touched. This behavior should be improved later.
        """
        y_right, y_left = data[x_right], data[x_left]
        if self._scale[1] == 0:
            return sqrt(((x_right - x_left) / self._scale[0])**2)
        else:
            return sqrt(((x_right - x_left) / self._scale[0])**2 +
                        ((y_right - y_left) / self._scale[1])**2)

    def loss(self, real=True):
        losses = self.losses if real else self.losses_combined
        if len(losses) == 0:
            return float('inf')
        else:
            return max(losses.values())

    def update_losses(self, x, data, neighbors, losses):
        x_lower, x_upper = neighbors[x]
        if x_lower is not None:
            losses[x_lower, x] = self.interval_loss(x_lower, x, data)
        if x_upper is not None:
            losses[x, x_upper] = self.interval_loss(x, x_upper, data)
        try:
            del losses[x_lower, x_upper]
        except KeyError:
            pass

    def find_neighbors(self, x, neighbors):
        pos = neighbors.bisect_left(x)
        x_lower = neighbors.iloc[pos-1] if pos != 0 else None
        x_upper = neighbors.iloc[pos] if pos != len(neighbors) else None
        return x_lower, x_upper

    def update_neighbors(self, x, neighbors):
        if x not in neighbors:  # The point is new
            x_lower, x_upper = self.find_neighbors(x, neighbors)
            neighbors[x] = [x_lower, x_upper]
            neighbors.get(x_lower, [None, None])[1] = x
            neighbors.get(x_upper, [None, None])[0] = x

    def update_scale(self, x, y):
        self._bbox[0][0] = min(self._bbox[0][0], x)
        self._bbox[0][1] = max(self._bbox[0][1], x)
        if y is not None:
            self._bbox[1][0] = min(self._bbox[1][0], y)
            self._bbox[1][1] = max(self._bbox[1][1], y)

        self._scale = [self._bbox[0][1] - self._bbox[0][0],
                       self._bbox[1][1] - self._bbox[1][0]]

    def add_point(self, x, y):
        real = y is not None

        if real:
            # Add point to the real data dict and pop from the unfinished
            # data_interp dict.
            self.data[x] = y
            try:
                del self.data_interp[x]
            except KeyError:
                pass
        else:
            # The keys of data_interp are the unknown points
            self.data_interp[x] = None

        # Update the neighbors
        self.update_neighbors(x, self.neighbors_combined)
        if real:
            self.update_neighbors(x, self.neighbors)

        # Update the scale
        self.update_scale(x, y)

        # Interpolate
        if not real:
            self.data_interp = self.interpolate()

        # Update the losses
        self.update_losses(x, self.data_combined, self.neighbors_combined,
                           self.losses_combined)
        if real:
            self.update_losses(x, self.data, self.neighbors, self.losses)

        if real:
            # If the scale has doubled, recompute all losses.
            if self._scale > self._oldscale * 2:
                    self.losses = {key: self.interval_loss(*key, self.data)
                                   for key in self.losses}
                    self.losses_combined = {key: self.interval_loss(*key,
                        self.data_combined) for key in self.losses_combined}
                    self._oldscale = self._scale


    def choose_points(self, n=10, add_data=True):
        """Return n points that are expected to maximally reduce the loss."""
        # Find out how to divide the n points over the intervals
        # by finding  positive integer n_i that minimize max(L_i / n_i) subject
        # to a constraint that sum(n_i) = n + N, with N the total number of
        # intervals.

        # Return equally spaced points within each interval to which points
        # will be added.
        if n == 0:
            return []

        # If the bounds have not been chosen yet, we choose them first.
        xs = []
        for bound in self.bounds:
            if bound not in self.data and bound not in self.data_interp:
                xs.append(bound)
        # Ensure we return exactly 'n' points.
        if xs:
            loss_improvements = [float('inf')] * n
            if n <= 2:
                return xs[:n], loss_improvements
            else:
                return np.linspace(*self.bounds, n), loss_improvements

        def points(x, n):
            if n == 1:
                return []
            else:
                step = (x[1] - x[0]) / n
                return [x[0] + step * i for i in range(1, n)]

        # Calculate how many points belong to each interval.
        quals = [(-loss, x_range, 1) for (x_range, loss) in
                 self.losses_combined.items()]

        heapq.heapify(quals)

        for point_number in range(n):
            quality, x, n = quals[0]
            heapq.heapreplace(quals, (quality * n / (n + 1), x, n + 1))

        xs = list(itertools.chain.from_iterable(points(x, n)
                  for quality, x, n in quals))

        loss_improvements = list(itertools.chain.from_iterable(
                                 itertools.repeat(-quality, n)
                                 for quality, x, n in quals))

        if add_data:
            self.add_data(points, itertools.repeat(None))

        return xs, loss_improvements

    def interpolate(self, extra_points=None):
        xs = list(self.data.keys())
        ys = list(self.data.values())
        xs_unfinished = list(self.data_interp.keys())

        if extra_points is not None:
            xs_unfinished += extra_points

        if len(ys) == 0:
            interp_ys = (0,) * len(xs_unfinished)
        else:
            interp_ys = np.interp(xs_unfinished, xs, ys)

        data_interp = {x: y for x, y in zip(xs_unfinished, interp_ys)}

        return data_interp

    def plot(self):
            if self.data:
                return hv.Scatter(self.data)
            else:
                return hv.Scatter([])

    def remove_unfinished(self):
        self.data_interp = {}
        self.losses_combined = copy(self.losses)
        self.neighbors_combined = copy(self.neighbors)


def dispatch(child_functions, arg):
    index, x = arg
    return child_functions[index](x)


class BalancingLearner(BaseLearner):
    """Choose the optimal points from a set of learners.

    Parameters
    ----------
    learners : sequence of BaseLearner
        The learners from which to choose. These must all have the same type.

    Notes
    -----
    This learner compares the 'loss' calculated from the "child" learners.
    This requires that the 'loss' from different learners *can be meaningfully
    compared*. For the moment we enforce this restriction by requiring that
    all learners are the same type but (depending on the internals of the
    learner) it may be that the loss cannot be compared *even between learners
    of the same type*. In this case the BalancingLearner will behave in an
    undefined way.
    """
    def __init__(self, learners):
        self.learners = learners

        # Naively we would make 'function' a method, but this causes problems
        # when using executors from 'concurrent.futures' because we have to
        # pickle the whole learner.
        self.function = functools.partial(dispatch, [l.function for l
                                                     in self.learners])

        if len(set(learner.__class__ for learner in self.learners)) > 1:
            raise TypeError('A BalacingLearner can handle only one type'
                            'of learners.')

    def _choose_and_add_points(self, n):
        points = []
        for _ in range(n):
            loss_improvements = []
            pairs = []
            for index, learner in enumerate(self.learners):
                point, loss_improvement = learner.choose_points(n=1, add_data=False)
                loss_improvements.append(loss_improvement[0])
                pairs.append((index, point[0]))
            x, _ = max(zip(pairs, loss_improvements), key=itemgetter(1))
            points.append(x)
            self.add_point(x, None)
        return points, None

    def choose_points(self, n, add_data=True):
        """Choses points for learners."""
        if not add_data:
            with restore(*self.learners):
                return self._choose_and_add_points(n)
        else:
            return self._choose_and_add_points(n)

    def add_point(self, x, y):
        index, x = x
        self.learners[index].add_point(x, y)

    def loss(self, real=True):
        return max(learner.loss(real) for learner in self.learners)

    def plot(self, index):
        return self.learners[index].plot()

    def remove_unfinished(self):
        """Remove uncomputed data from the learners."""
        for learner in self.learners:
            learner.remove_unfinished()


def _max_disagreement_location_in_simplex(points, values, grad, transform):
    """Find the point of maximum disagreement between linear and quadratic model

    Parameters
    ----------
    points : (ndim+1, ndim)
        Locations
    values : (ndim+1)
        Values
    grad : (ndim+1, ndim)
        Gradients

    Notes
    -----
    Based on maximizing the disagreement between a linear and a cubic model::

        f_1(x) = a + sum_j b_j (x_j - x_0)
        f_2(x) = a + sum_j c_j (x_j - x_0) + sum_ij d_ij (x_i - x_0) (x_j - x_0)
                   + sum_ijk e_ijk (x_i - x_0) (x_j - x_0) (x_k - x_0)

        |f_1(x) - f_2(x)|^2 = max!

    The parameter a, b are estimated from values of the function, and the
    parameters c, d, e from values and gradients.

    """
    ndim = points.shape[1]
    m = points.shape[0]
    values = values.ravel()

    x = points - points[-1]
    z = values - values[-1]

    # -- Least-squares fit: (i) linear model
    b, _, _, _ = np.linalg.lstsq(x[:-1], z[:-1])

    # -- Least-squares fit: (ii) cubic model

    # (ii.a) fitting function values
    x2 = (x[:-1, :, None] * x[:-1, None, :]).reshape(m-1, ndim*ndim)
    x3 = (x[:-1, :, None, None] * x[:-1, None, :, None] * x[:-1, None, None, :]
          ).reshape(m-1, ndim*ndim*ndim)
    lhs1 = np.c_[x[:-1], x2, x3]
    rhs1 = z[:-1]

    # (ii.b) fitting gradients
    d_b = np.tile(np.eye(ndim)[None, :, :], (m, 1, 1)).reshape(m*ndim, ndim)

    o = np.eye(ndim)

    d_d = (o[None, :, None, :] * x[:, None, :, None] +
           x[:, None, None, :] * o[None, :, :, None]).reshape(m * ndim, ndim * ndim)
    d_e = (
        o[:, None, :, None, None] *
        x[None, :, None, :, None] * x[None, :, None, None, :]
        +
        x[None, :, :, None, None] *
        o[:, None, None, :, None] * x[None, :, None, None, :]
        +
        x[None, :, :, None, None] *
        x[None, :, None, :, None] * o[:, None, None, None, :]
    ).reshape(m*ndim, ndim*ndim*ndim)

    lhs2 = np.c_[d_b, d_d, d_e]
    rhs2 = grad.ravel()

    # (ii.c) fit it
    lhs = np.r_[lhs1, lhs2]
    rhs = np.r_[rhs1, rhs2]
    cd, _, rank, _ = np.linalg.lstsq(lhs, rhs)
    c = cd[:ndim]
    d = cd[ndim:ndim+ndim*ndim].reshape(ndim, ndim)
    e = cd[ndim+ndim*ndim:].reshape(ndim, ndim, ndim)

    # -- Find point of maximum disagreement, inside the triangle

    itr = np.linalg.inv(transform[:-1])

    def func(x):
        x = itr.dot(x)
        v = -abs(((c - b)*x).sum()
                 + (d*x[:, None]*x[None, :]).sum()
                 + (e*x[:, None, None]*x[None, :, None]*x[None, None, :]).sum()
                 )**2
        return np.array(v)

    cons = [lambda x: np.array([1 - x.sum()])]
    for j in range(ndim):
        cons.append(lambda x: np.array([x[j]]))

    ps = [1.0/(ndim+1)] * ndim
    p = optimize.fmin_slsqp(func, ps, ieqcons=cons, disp=False,
                            bounds=[(0, 1)]*ndim)
    p = itr.dot(p) + points[-1]

    return p


class Learner2D(BaseLearner):

    """Sample a 2-D function adaptively.

    Parameters
    ----------
    function : callable
        The function to learn. Must take a tuple of two real
        parameters and return a real number.
    bounds : list of 2-tuples
        A list ``[(a1, b1), (a2, b2)]`` containing bounds,
        one per dimension.
    dtype : dtype, optional
        Type of data from function. Default: float (real-valued)

    Attributes
    ----------
    points
        Sample points so far including the unknown interpolated ones.
    values
        Sampled values so far including the unknown interpolated ones.
    points_real
        Sample points so far.
    values_real
        Sampled values so far.

    Notes
    -----
    Requires Scipy >= 0.9.0.

    The sample points are chosen by estimating the point where the
    linear and cubic interpolants based on the existing points have
    maximal disagreement. This point is then taken as the next point
    to be sampled.

    In practice, this sampling protocol results to sparser sampling of
    smooth regions, and denser sampling of regions where the function
    changes rapidly, which is useful if the function is expensive to
    compute.

    This sampling procedure is not extremely fast, so to benefit from
    it, your function needs to be slow enough to compute.

    Examples
    --------
    See below.

    """

    def __init__(self, function, bounds, dtype=float):
        self.function = function
        self.ndim = len(bounds)
        if self.ndim != 2:
            raise ValueError("Only 2-D sampling supported.")
        self.bounds = tuple([(float(a), float(b)) for a, b in bounds])
        self._points = np.zeros([100, self.ndim])
        self._values = np.zeros([100], dtype)
        self.n = 0
        self.nstack = 10
        self._stack = []
        self._interp = {}

        pts = []
        for j, (a, b) in enumerate(self.bounds):
            if j == 0:
                def _append(x):
                    pts.append((x,))
            else:
                opts = pts
                pts = []

                def _append(x):
                    for r in opts:
                        pts.append(r + (x,))
            _append(a)
            _append(b)
        self._stack = pts

    @property
    def points(self):
        return self._points[:self.n]

    @property
    def values(self):
        return self._values[:self.n]

    @property
    def points_real(self):
        return np.delete(self.points, list(self._interp.values()), axis=0)

    @property
    def values_real(self):
        return np.delete(self.values, list(self._interp.values()), axis=0)

    @property
    def n_real(self):
        return self.n - len(self._interp)

    def add_point(self, point, value):
        nmax = self.values.shape[0]
        if self.n >= nmax:
            self._values = np.resize(self._values, [2*nmax + 10])
            self._points = np.resize(self._points, [2*nmax + 10, self.ndim])

        point = tuple(point)
        if value is None:
            self._interp[point] = self.n
            old_point = False
        else:
            old_point = point in self._interp

        if old_point:
            n = self._interp.pop(point)
            self._points[n] = point
            self._values[n] = value
        else:
            self._points[self.n] = point
            self._values[self.n] = value
            self.n += 1

    def _fill_stack(self, stack_till=None):
        # Deal with periodicity: extend by one period
        p = self.points
        v = self.values

        if v.shape[0] < self.ndim+1:
            raise ValueError("too few points...")

        # Interpolate the unfinished points
        if self._interp:
            if self.n_real >= 4:
                ip = interpolate.LinearNDInterpolator(self.points_real,
                                                      self.values_real)
            else:
                ip = lambda x: np.empty(len(x))  # Important not to return exact zeros
            n_interp = list(self._interp.values())
            values = ip(p[n_interp])
            for n, value in zip(n_interp, values):
                v[n] = value

        # Interpolate
        ip = interpolate.LinearNDInterpolator(p, v)
        tri = ip.tri

        # Gradients
        grad = interpolate.interpnd.estimate_gradients_2d_global(
            tri, ip.values.ravel(), tol=1e-6)

        p = tri.points[tri.vertices]
        g = grad[tri.vertices]
        v = ip.values.ravel()[tri.vertices]

        dev = 0
        for j in range(self.ndim):
            vest = v[:, j, None] + ((p[:, :, :] -
                                     p[:, j, None, :]) *
                                     g[:, j, None, :]).sum(axis=-1)
            dev += abs(vest - v).max(axis=1)

        q = p[:, :-1, :] - p[:, -1, None, :]
        vol = abs(q[:, 0, 0]*q[:, 1, 1] - q[:, 0, 1]*q[:, 1, 0])
        vol /= special.gamma(1 + self.ndim)

        dev *= vol

        if stack_till is None:
            # Take new points
            try:
                cp = 0.9*dev.max()
                nstack = min(self.nstack, (dev > cp).sum())
                if nstack <= 0:
                    raise ValueError()
            except ValueError:
                nstack = 1
        else:
            nstack = stack_till

        def point_exists(p):
            eps = np.finfo(float).eps * self.points.ptp() * 100
            if abs(p - self.points).sum(axis=1).min() < eps:
                return True
            if self._stack:
                if abs(p - np.asarray(self._stack)).sum(axis=1).min() < eps:
                    return True
            return False

        for j in range(len(dev)):
            jsimplex = np.argmax(dev)
            # -- Estimate point of maximum curvature inside the simplex
            p = tri.points[tri.vertices[jsimplex]]
            v = ip.values[tri.vertices[jsimplex]]
            g = grad[tri.vertices[jsimplex]]
            transform = tri.transform[jsimplex]

            point_new = _max_disagreement_location_in_simplex(
                p, v, g, transform)

            # -- Reduce to bounds
            for j, (a, b) in enumerate(self.bounds):
                point_new[j] = max(a, min(b, point_new[j]))

            # -- Check if it is really new (also, revert to mean point optionally)
            if point_exists(point_new):
                dev[jsimplex] = 0
                continue

            # Add to stack
            self._stack.append(tuple(point_new))

            if len(self._stack) >= nstack:
                break
            else:
                dev[jsimplex] = 0

    def _choose_and_add_points(self, n):
        if n <= len(self._stack):
            points = self._stack[:n]
            self.add_data(points, itertools.repeat(None))
            self._stack = self._stack[n:]
        else:
            points = []
            n_left = n
            while n_left > 0:
                if self.n >= 2**self.ndim:
                    self._fill_stack(stack_till=max(n_left, self.nstack))
                from_stack = self._stack[:n_left]
                points += from_stack
                self.add_data(from_stack, itertools.repeat(None))
                self._stack = self._stack[n_left:]
                n_left -= len(from_stack)

        loss_improvements = [1] * n
        return points, loss_improvements

    def choose_points(self, n, add_data=True):
        if not add_data:
            with restore(self):
                points, loss_improvements = self._choose_and_add_points(n)
        else:
            points, loss_improvements = self._choose_and_add_points(n)
        return points, loss_improvements

    def loss(self):
        return self.n_real

    def remove_unfinished(self):
        self._points = self.points_real
        self._values = self.values_real
        self.n -= len(self._interp)
        self._interp = {}

    def plot(self, n_x=201, n_y=201):
        if self.n_real >= 4:
            x = np.linspace(*self.bounds[0], n_x)
            y = np.linspace(*self.bounds[1], n_y)

            ip = interpolate.LinearNDInterpolator(self.points_real,
                                                  self.values_real)
            z = ip(x[:, None], y[None, :])
            return hv.Image(z)
        else:
            return hv.Image(np.zeros((2, 2)))

    def __getstate__(self):
        return (self._values.copy(),
                self._points.copy(),
                copy(self._interp),
                copy(self._stack),
                self.n)

    def __setstate__(self, state):
        self._values, self._points, self._interp, self._stack, self.n = state


@contextmanager
def restore(*learners):
    states = [learner.__getstate__() for learner in learners]
    try:
        yield
    finally:
        for state, learner in zip(states, learners):
            learner.__setstate__(state)
