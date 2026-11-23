"""Battle win-percentage calculator for Axis & Allies 1942 (2nd Edition), land units only.

Modules:
    units     -- unit stats, army tuple layout, text parsing, IPC cost.
    solver    -- exact win/loss/draw probabilities via a memoized Markov chain.
    simulate  -- Monte Carlo dice simulation used to cross-check the solver.
    cli       -- command-line front end (``python -m aa1942.cli``).
"""
