FAQ: frequently asked questions
-------------------------------

How do I get the data?
~~~~~~~~~~~~~~~~~~~~~~

Check ``learner.data``.


How do I learn more than one value per point?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use the `adaptive.DataSaver`.


My runner failed, how do I get the error message?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Check ``runner.task.print_stack()``.


How do I get a `~adaptiveLearner2D`\'s data on a grid?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``learner.interpolated_on_grid()`` optionally with a argument ``n`` to specify the the amount of points in ``x`` and ``y``.


I get "``concurrent.futures.process.BrokenProcessPool``: A process in the process pool was terminated abruptly while the future was running or pending." what does it mean?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


What is the difference with FEM?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


What is the difference with Bayesian optimization?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is a really good question.
Indeed there are similarities between what we do and Bayesian optimization.

The choice of new points is based on the previous ones.

There is a tuneable algorithm for performing this selection, and the easiest way to formulate this algorithm is by defining a loss function.

Bayesian optimization is a perfectly fine algorithm for choosing new points within adaptive. As an experiment we have interfaced ``scikit-optimize`` and implemented a learner that just wraps it.

However there are important differences why Bayesian optimization doesn't cover all the needs. Often our aim is to explore the function and not minimize it. Further AFAIK Bayesian optimization is most often combined with Gaussian processes because it is then possible to compute the posteriour exactly and formulate a rigorous optimization strategy.
Unfortunately Gaussian processes are computationally expensive and won't be useful with tens of thousands of points.
adaptive is much more simple-minded and it relies only on the local properties of the data, rather than fitting it globally.

We'd say that Bayesian modeling is good for really computationally expensive data, regular grids for really cheap data, and local adaptive algorithms forare somewhere in the middle.



What is the difference with adaptive meshing in CFD or computer graphics?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


How to use Adaptive with MATLAB?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


Missing a question that you think belongs here? Let us `know <https://github.com/python-adaptive/adaptive/issues/new>`_.
