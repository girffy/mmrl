import math
import pickle
import datetime
import calendar
import pytz
import sys
from scipy.stats import norm
from swiglpk import *

import data
import config

INF = float('inf')

class ReplayLabeller:
  def __init__(self, player_file, challonge_file, setup_file):
    with open(challonge_file, 'rb') as cfile:
      dat = pickle.load(cfile)
      self.matches = dat['matches']
      self.participants = dat['participants']

    with open(setup_file, 'rb') as sfile:
      self.setups = pickle.load(sfile)

    # dict mapping a challonge player id to their challonge display name
    self.playerid_map = {p['id']:p['display-name'] for p in self.participants}

    # dict mapping a tag fingerprint to their mains/secondaries
    self.main_map = data.parse_player_file(player_file)

    # setup the distribution pdfs for the differences between challonge
    # start/end time and replay start/end time
    # TODO: part of these distributions are cut off by the TIME_SLACK logic; we
    # should normalize them here to have integral 1
    self.start_pdf = norm(config.ANNOUNCE_TO_START_MEAN, config.ANNOUNCE_TO_START_SD).pdf
    self.end_pdf = norm(config.END_TO_REPORT_MEAN, config.END_TO_REPORT_SD).pdf

  # compute the log-likelihood of a match having produced the given replays
  def compute_total_ll(self, match, replays):
    time_ll = self.compute_time_ll(match, replays)
    char_logprob = self.compute_char_logprob(match, replays)

    total_ll = time_ll + char_logprob

    return total_ll

  # I hate python's date/time handling so much :|
  def time_diff(self, dt1, dt2):
    return calendar.timegm(dt1.astimezone(pytz.timezone(config.TIME_ZONE)).timetuple()) -\
           calendar.timegm(dt2.astimezone(pytz.timezone(config.TIME_ZONE)).timetuple())

  # compute the log-likelihood of a match having produced these replay timings
  def compute_time_ll(self, match, replays):
    start_diff = self.time_diff(replays[0]['start_time'], match['started-at'])
    end_diff = self.time_diff(match['completed-at'], replays[-1]['end_time'])

    if start_diff < -config.TIME_SLACK or end_diff < -config.TIME_SLACK:
      return -INF

    start_l = self.start_pdf(start_diff)
    end_l = self.end_pdf(end_diff)

    if start_l == 0 or end_l == 0:
      return -INF

    start_ll = max(config.MIN_START_LL, math.log(start_l))
    end_ll = max(config.MIN_END_LL, math.log(end_l))


    time_ll = start_ll + end_ll

    return time_ll

  # compute the log-probability of a match having produced these ports,
  # characters, and win pattern
  def compute_char_logprob(self, match, replays):
    # assume that controller ports are never changed within a match
    portsets = {tuple([i for i,p in enumerate(game['ports']) if p != None])
                for game in replays}
    if len(portsets) > 1:
      return -INF # inconsistent ports
    a, b = list(portsets)[0]

    awins = sum([not game['ports'][a]['dead_at_end'] for game in replays])
    bwins = sum([not game['ports'][b]['dead_at_end'] for game in replays])

    # based on match score, infer which player was on which port
    if awins == match['player1_score'] and bwins == match['player2_score']:
      p1port = a
      p2port = b
    elif awins == match['player2_score'] and bwins == match['player1_score']:
      p1port = b
      p2port = a
    else:
      # match score does not make sense with the wins that each port had
      return -INF

    # filter out impossible cases like win-win-loss in a bo3, by verifying that
    # the winner won the last game
    winner = a if awins > bwins else b
    if replays[-1]['ports'][winner]['dead_at_end']:
      return -INF # invalid best-of-n results

    # compute the total log-probability of these character selections,
    # normalized by the match length so that sets of different lengths are
    # comparable to each other
    total_char_logprob = 0
    for player in [1,2]:
      port = p1port if player == 1 else p2port
      tagfp = data.tag_fingerprint(self.playerid_map[match['player%s-id' % player]])

      # if we don't know what this player's mains are, use the default
      # probability
      if tagfp not in self.main_map:
        total_char_logprob += math.log(config.DEFAULT_PROB)
        continue

      mains, secs = self.main_map[tagfp]

      # split up the probability between mains and secondaries. e.g. if
      # MAIN_CHAR_PROB=.8 and SEC_CHAR_PROB=.1, then .8 probability is split up
      # evenly among all the player's mains, and .1 probability is split up
      # between the secondaries, but if the player has no secondaries, .9
      # probability is split up among the mains (and vice-versa)
      if len(mains) == 0 and len(secs) == 0:
        total_char_logprob += math.log(config.DEFAULT_PROB)
        continue
      elif len(secs) == 0:
        main_prob = config.MAIN_CHAR_PROB + config.SEC_CHAR_PROB
        sec_prob = 0
      elif len(mains) == 0:
        main_prob = 0
        sec_prob = config.MAIN_CHAR_PROB + config.SEC_CHAR_PROB
      else:
        main_prob = config.MAIN_CHAR_PROB
        sec_prob = config.SEC_CHAR_PROB

      # based on the above assumptions, sum the log_probability for each of this
      # player's selections, and divide by number of replays. This is equivalent
      # to taking the log of the geometric mean of the character probabilities
      for game in replays:
        if game['ports'][port]['char'] in mains:
          total_char_logprob += math.log(main_prob / len(mains)) / len(replays)
        elif game['ports'][port]['char'] in secs:
          total_char_logprob += math.log(sec_prob / len(secs)) / len(replays)
        else:
          total_char_logprob += math.log( (1 - main_prob - sec_prob) * config.DEFAULT_PROB )\
                                / len(replays)

    return total_char_logprob

  # produce a list all_labels of all the possible replay labels for each match, ordered by
  # confidence, so that all_labels[mi][k] is the k-th best label, and is the
  # triple (ll, si, ri), where ll is the log-likelihood of the label and si, ri
  # are the setup and game indices, respectively
  def compute_all_labels(self):
    label_counts = {si:0 for si in range(len(self.setups))}
    all_labels = [[] for match in self.matches]
    for mi, match in enumerate(self.matches):
      ngames = match['num_games']
      for si, setup in enumerate(self.setups):
        for ri, replay in enumerate(setup['replays']):
          if len(setup['replays']) <= ri + ngames - 1:
            continue

          replays = setup['replays'][ri : ri+ngames]

          if any([r['numplayers'] != config.REQ_NUM_PLAYERS for r in replays]):
            continue

          total_ll = self.compute_total_ll(match, replays)

          if total_ll >= config.NOLABEL_OBJVAL:
            all_labels[mi].append(( total_ll, si, ri ))
            label_counts[si] += 1
      all_labels[mi].sort(reverse=True)

    for si in range(len(self.setups)):
      print("Setup '%s': has %s replays -> %s labels" %
        (self.setups[si]['drive'], len(self.setups[si]['replays']), label_counts[si]))

    return all_labels

  # given the list of matches, and output from ReplayLabeller.compute_all_labels,
  # construct a glpk MIP instance for the problem and solve it. forced_labels
  # is a set containing triples (mi, si, ri) indicating that mi must be
  # labelled with (si, ri), and/or pairs (mi, None) indicating mi must be left
  # unlabelled.
  def mip_solve(self, all_labels, forced_labels = set()):
    replays = list({(si, ri) for lbls in all_labels for _, si, ri in lbls})

    N = sum([len(lbls) for lbls in all_labels]) + len(self.matches) # number of variables
    M = len(self.matches) + len(replays) # number of constraints

    # number of nonzero entries in constraint matrix
    nze = sum([(m['num_games']+1)*len(lbls)+1 for m,lbls in zip(self.matches, all_labels)])

    ia = intArray(1+nze) # primal constraint indices
    ja = intArray(1+nze) # primal variable indices
    ar = doubleArray(1+nze) # nonzero values of constraint matrix
    a_idx = 1 # the next index in ar to use

    # update the state of ia, ja, ar, a_idx to add the entry A[i,j] = val to the
    # constraint matrix A
    def add_cm_entry(i, j, val, a_idx):
      ia[a_idx] = i
      ja[a_idx] = j
      ar[a_idx] = val

    mip = glp_create_prob()
    glp_set_prob_name(mip, "replay_label_MIP")
    glp_set_obj_dir(mip, GLP_MAX)

    # initialize primal variables
    glp_add_cols(mip, N)
    lvars = {} # dict mapping a triple mi, si, ri to its glpk variable index
    var_idx = 1
    for mi, lbls in enumerate(all_labels):
      for ll, si, ri in lbls:
        lvars[mi, si, ri] = var_idx
        glp_set_col_name(mip, var_idx, "M%s_s%sr%s" % (mi, si, ri))
        if (mi, si, ri) in forced_labels:
          glp_set_col_bnds(mip, var_idx, GLP_FX, 1.0, 1.0)
        else:
          glp_set_col_bnds(mip, var_idx, GLP_DB, 0.0, 1.0)
        glp_set_obj_coef(mip, var_idx, ll)
        glp_set_col_kind(mip, var_idx, GLP_IV)
        var_idx += 1
    for mi in range(len(self.matches)):
      glp_set_col_name(mip, var_idx, "M%s_unlabelled" % mi)
      if (mi, None) in forced_labels:
        glp_set_col_bnds(mip, var_idx, GLP_FX, 1.0, 1.0)
      else:
        glp_set_col_bnds(mip, var_idx, GLP_DB, 0.0, 1.0)
      glp_set_obj_coef(mip, var_idx, config.NOLABEL_OBJVAL)
      var_idx += 1


    # initialize primal constraints, and populate constraint matrix
    # the constraint population is pretty inefficient, but probably isn't a
    # bottleneck anyway
    glp_add_rows(mip, M)
    row_idx = 1
    a_idx = 1
    # match constraints: each match has total probability 1 for its labels
    for mi, lbls in enumerate(all_labels):
      glp_set_row_name(mip, row_idx, "M%s=1" % mi)
      glp_set_row_bnds(mip, row_idx, GLP_FX, 1.0, 1.0)

      add_cm_entry(row_idx, len(lvars)+mi+1, 1.0, a_idx)
      a_idx += 1
      for (vmi, vsi, vri), j in lvars.items():
        if vmi == mi:
          add_cm_entry(row_idx, j, 1.0, a_idx)
          a_idx += 1
      row_idx += 1
    # replay constraints: each replay has total probability at most 1 for its
    # labels
    for si, ri in replays:
      glp_set_row_name(mip, row_idx, "s%sr%s<=1" % (si,ri))
      glp_set_row_bnds(mip, row_idx, GLP_UP, 1.0, 1.0)

      for (vmi, vsi, vri), j in lvars.items():
        if vsi == si and vri <= ri < vri + self.matches[vmi]['num_games']:
          add_cm_entry(row_idx, j, 1.0, a_idx)
          a_idx += 1
      row_idx += 1

    glp_load_matrix(mip, a_idx-1, ia, ja, ar)

    parm = glp_iocp()
    glp_init_iocp(parm)
    parm.presolve = GLP_ON
    glp_intopt(mip, parm)

    # extract the solution from the mip
    soln = [None for _ in self.matches]
    llmap = {(mi, si, ri) : ll
             for mi in range(len(self.matches))
             for ll, si, ri in all_labels[mi]}
    for (mi, si, ri), j in lvars.items():
      val = glp_mip_col_val(mip, j)
      assert val in [0,1]
      if val == 1:
        soln[mi] = llmap[mi,si,ri], si, ri

    objval = glp_mip_obj_val(mip)
    print("MIP solved; objval=%.2f, labelled %s/%s matches" %
      (objval, len([s for s in soln if s != None]), len(self.matches)))
    return objval, soln

  # for a given match, find the log-likelihood of the best solution for each of
  # its labels, and use this to estimate the probability of each label. If
  # include_nolabel is true-ish, then the option of providing no label is also
  # included. Omit results with probability less than threshold
  def get_indiv_rankings(self, all_labels, mi, include_nolabel=True, normalize=True, threshold=0.0):
    labels = []
    for _, si, ri in all_labels[mi]:
      objval, soln = self.mip_solve(all_labels, forced_labels = {(mi, si, ri)})
      labels.append([objval, si, ri])

    if include_nolabel:
      ul_objval, ul_soln = self.mip_solve(all_labels, forced_labels = {(mi, None)})
      labels.append([ul_objval, None, None])

    if normalize:
      # compute the relative probability of each solution
      mean = sum([lbl[0] for lbl in labels]) * 1.0 / len(labels)
      for lbl in labels:
        lbl[0] = math.exp(lbl[0] - mean)
      total_l = sum([lbl[0] for lbl in labels])
      for lbl in labels:
        lbl[0] /= total_l
      labels.sort(reverse=True)

    return [lbl for lbl in labels if lbl[0] >= threshold]

  # given the likelihoods of each label, estimate the *probability* of each
  # label. Does this at the match level by comparing the likelihoods of each
  # feasible label (including having no label at all) for this match.
  def get_all_labels_probs(self, all_labels, include_nolabel=True, normalize=True, threshold=0.0):
    soln = [self.get_indiv_rankings(all_labels, mi, include_nolabel, normalize, threshold)
            for mi in range(len(self.matches))]
    return soln
