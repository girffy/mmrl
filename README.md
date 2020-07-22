## mmrl
mmrl (MLE/MILP Replay Labeller) is a tool for automatically identifying ssbm replays for tournament matches. It reads a bracket from challonge, and a set of slippi replays from various setups, and attempts to identify the replays of each bracket match.  It primarily goes off of timestamps; it is assumed that game 1 of a match will start shortly after the match is called by the TO (and started in challonge), and the match is reported to challonge shortly after the last game ends. A .csv containing the mains/secondaries of (some of) the players is also optionally used. The workflow also looks at the set count and makes sure the replays are consistent with it.

A website that uses this program to generate replay labels for the Google MTV melee community can be found at http://csclub.uwaterloo.ca/~b2coutts/mtv_replays.

## Dependencies
* python3, with packages:
	* pychallonge
	* py-slippi
	* swiglpk
	* pandas
	* scipy
	* pickle
* libglpk-dev

## Running the Workflow
`mmrl.py` is the file that runs each part of the work flow. It must be given a directory to write its files to, and takes some optional flags to tell it which
tasks to do:

* `-c tournament_id` fetches challonge bracket data. `tournament_id` must be usable by [tournaments/index](https://api.challonge.com/v1/documents/tournaments/show), and is usually of the form `account_name-tournament_name`. This option can be supplied multiple times to provide multiple tournaments, e.g. to include an amateur bracket. This generates the file `challonge_data.p`
* `-s slippi_dir` parses slippi replay data. `slippi_dir` should be a directory containing directories named `Drive #K` for some number K. All replays from each of these directories are parsed, and written to `slippi_data.p`
* `-l` runs the replay labeller; this requires the files from the `-c` and `-s` steps to be there. This writes every label (i.e. for each match, every plausible replay set it could have generated) to `full_output.txt`, and also generates a single best guess (or no label) for each match, written to `lp_output.txt`.  If the option `-p player_csv` is also supplied, then `player_csv` will be parsed to identify players' mains. `player_csv` should have at least the columns `TAG`, `Main`, and `Secondaries`, where the latter two columns contain zero or more melee character names.


## Technical Stuff

The code uses maximum likelihood estimation (MLE) to find the most likely replays for each tournament match. It does so by establishing some assumptions about how the replays of a match are likely to be distributed.  Given a tournament match, its corresponding replays are assumed to have the following properties:

* The time between the match starting in challonge and game 1 beginning is normally distributed (with parameters specified in `config.py`)
* The time between the last game ending and the match being reported in challonge is normally distributed (with parameters specified in `config.py`
* Both players use the same port throughout the set
* All games within a set are played on the same setup
* In each game, each player has probability X of using one of their mains, probability Y of using one of their secondaries, and probability (1-X-Y) of using some other character (X and Y are defined in `config.py`)

Using these assumptions, for any n-game match M, and any set R of n consecutive replays on the same setup, we can estimate the likelihood L(M,R) of M producing the replays R. Subsequently, we can estimate the overall likelihood of a particular assignment of replays to matches by multiplying the individual likelihoods of each assignment.  Then, we can phrase the problem of trying to label these matches as an optimization problem: we are trying to find a maximum likelihood assignment of replays to each match, such that no replay is assigned to more than 1 match. Using the common trick of maximizing log-likelihood instead of likelihood, the problem we're solving is:

> Find an assignment function a, which takes a n-game match M and assigns it a sequence a(M) of n consecutive replays on the same setup, such that no replay is assigned to two matches, and the sum of log(L(M, a(M)) (i.e. the sum of the log-likelihoods of the assignments) is maximized.

We also want to have the option of not labelling a match at all, which will carry some fixed penalty. This problem is similar to the well-known assignment problem, with the difference that multiple replays are needed for each match. I suspect this problem is NP-hard, though I haven't been able to prove it.  The problem is solved by formulating it as a mixed integer linear program (MILP), and solving it with GLPK.

## Modelling as a MILP

The MILP is explicitly constructed in the `mip_solve` function in `ReplayLabeller.py`; this section describes at a high-level how the problem is modelled as a
MILP. Let M be the set of matches, and let R be the set of replays. For each m in M and r in R, we will have a binary variable x_\{m,r\}; if this variable is 1,
it indicates that r is the first of the replays assigned to the match m. One difference in the code is that we only introduce the variables which represent
feasible assignments, and we also include a "dummy" label for each m, indicating that no replays were found for m, which carries a configurable constant
log-likelihood (NOLABEL_OBJVAL, default -25.0). As outlined above, we wish to maximize the log-likelihood log(L(m,r)) of these assignments. We'll need two sets
of constraints; the match-level constraints and the replay-level constraints.

The match-level constraints are that, for each match m in M, exactly one replay (or the dummy label, indicating no replay) is assigned to it. Note that a replay
r being assigned to m, indicates that r is the first of m's replays.

The replay-level constraints are that, for each replay r in R, r is not attributed to more than 1 match (noting that r may be attributed to a match either by
appearing in a x_\{m,r\} variable, or by having a preceding replay appear in a variable). Equivalently, for every replay r, we want there to be at most one
variable x_\{m,r'\} = 1, such that if match m starts with replay r', then it includes replay r. For notational brevity, let len(m) denote the number of games in
a match m, and for a replay r and integer k >= 0, let r-k be the replay occuring k before r on the same setup. Then, the MILP formulation is:

```
  max sum_\{m in M\} \sum_\{r in R\} l(m,r)
so that sum_\{r in R\} x_\{m,r\} = 1                                 for all m in M
        sum_\{m in M\} \sum_\{k=0\}^\{len(m)-1\} x_\{m,r-k\} <= 1    for all r in R
        x_\{m,r\} >= 0                                               for all m in M, r in R
```

An optimal solution to this MILP represents an optimal assignment of our matches to replays.


## Open Questions
* Is the optimization problem really NP-hard?
* How scalable is this approach? MILPs are very hard to solve, and may not be tractable for larger tournaments
* Is there a good way of accounting for an X% chance of a match not having an associated replay (due to a setup malfunctioning, the timings not being recorded properly in challonge, etc)?
* Is there a tractable way to compute the **probability** of a particular match's labelled replays being the true replays? In principle, for an assignment of M to R, we want to compute the total likelihood of all solutions that assign M to R, divided by the total likelihood of all solutions, but I don't know if there's a computationally feasible way to do this
