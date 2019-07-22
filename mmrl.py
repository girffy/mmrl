#!/usr/bin/env python3
import sys
import os
import datetime
import pytz
import pickle
import argparse

from ReplayLabeller import ReplayLabeller
import data
import config

desc = """ 
A tool for fetching challonge data, parsing slippi replays, and matching
challonge sets to their replays.

Example of a full run with a main bracket and an amateur bracket:
%(prog)s -c mtvmelee-122 \\
  -c mtvmelee-122_amateur \\
  -s slippi/MTVMelee122 \\
  -p smashers.csv \\
  -l \\
  labels_122
"""

if __name__ == '__main__':
  parser = argparse.ArgumentParser(description = desc,
    formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument("-c", metavar="challonge_id", action="append",
    help="(repeatable) fetch challonge data from these bracket id(s)")
  parser.add_argument("-s", metavar="slippi_dir",
    help="parse slippi replays from this directory")
  parser.add_argument("-p", metavar="player_csv",
    help="use csv for hints about players' mains")
  parser.add_argument("-l", help="label replays", action="store_true")
  parser.add_argument("output_dir", help="write output files to this dir")
  args = parser.parse_args()

  os.makedirs(args.output_dir, exist_ok=True)
  challonge_file = os.path.join(args.output_dir, config.CHALLONGE_FILE)
  slippi_file = os.path.join(args.output_dir, config.SLIPPI_FILE)
  full_output_file = os.path.join(args.output_dir, config.FULL_OUTPUT_FILE)
  single_output_file = os.path.join(args.output_dir, config.SINGLE_OUTPUT_FILE)
  prob_output_file = os.path.join(args.output_dir, config.PROB_OUTPUT_FILE)

  if args.c != None:
    print("Fetching challonge brackets: %s" % (', '.join(args.c)))
    data.fetch_brackets_to_file(args.c, challonge_file)

  if args.s != None:
    print("Parsing slippi data from %s" % args.s)
    data.parse_all_slp_drives(args.s, slippi_file)

  if args.l:
    replayLabeller = ReplayLabeller(args.p, challonge_file, slippi_file)

    print("Computing labels for %s matches..." % len(replayLabeller.matches))
    all_labels = replayLabeller.compute_all_labels()
    sl_objval, single_labels = replayLabeller.mip_solve(all_labels)
    probs_labels = replayLabeller.get_all_labels_probs(all_labels, threshold=0.05)

    matches = replayLabeller.matches
    setups = replayLabeller.setups

    def display_time(dt):
      return dt.astimezone(pytz.timezone(config.TIME_ZONE)).strftime('%Y-%m-%d %H:%M:%S')

    def print_match(fp, mi, match):
      fp.write("Match %s: %s vs %s [%s],  from %s to %s\n" %
        (mi,
         replayLabeller.playerid_map[match['player1-id']],
         replayLabeller.playerid_map[match['player2-id']],
         match['scores-csv'],
         display_time(match['started-at']),
         display_time(match['completed-at'])))

    def print_label(fp, ll, si, ri, ngames, prob=None, format_pct = False):
      llstr = ('%.2f%%' % (ll*100)) if format_pct else ('%.3f' % ll)
      probstr = '' if prob == None else (' (%.2f%%)' % (prob*100))
      fp.write("    %s%s: s%s %s Games %s-%s:  %s to %s\n" %
        (llstr, probstr, si, setups[si]['drive'], ri, ri+ngames-1,
         display_time(setups[si]['replays'][ri]['start_time']),
         display_time(setups[si]['replays'][ri+ngames-1]['end_time'])))

    def print_replay(fp, replay):
      chars = [p['char'] for p in replay['ports'] if p != None]
      wins = ['L' if p['dead_at_end'] else 'W' for p in replay['ports'] if p != None]
      fp.write("        %s to %s:  [%s]  %s (%s) vs. %s (%s)\n" %
        (display_time(replay['start_time']),
         display_time(replay['end_time']), replay['stage'], chars[0],
         wins[0], chars[1], wins[1]))

    # displays a solution with (up to) a single solution for each match, in the
    # format given e.g. by compute_greedy_labels and analyze_LP_soln. Returns
    # the average match ll of the solution and the number of missed matches.
    def print_single_soln(fp, soln):
      missed_mis = {mi for mi, lbl in enumerate(soln) if lbl == None}
      labels = {(mi,lbl) for mi, lbl in enumerate(soln) if lbl != None}
      for mi, (ll, si, ri) in sorted(labels, key = lambda x: x[1], reverse=True):
        print_match(fp, mi, matches[mi])
        print_label(fp, ll, si, ri, matches[mi]['num_games'])
        for k in range(matches[mi]['num_games']):
          print_replay(fp, setups[si]['replays'][ri+k])
        fp.write("\n")

      fp.write("\nMissed %s matches:\n" % len(missed_mis))
      for mi in missed_mis:
        print_match(fp, mi, matches[mi])

    # displays a solution with zero or more solutions for each match, in the
    # format of all_labels
    def print_full_soln(fp, soln, format_pct = False, sort_score = False):
      mims = list(enumerate(matches))
      if sort_score:
        mims.sort(key = lambda x: soln[x[0]][0], reverse=True)
      for mi, match in mims:
        print_match(fp, mi, match)
        for ll, si, ri in soln[mi]:
          if si != None:
            print_label(fp, ll, si, ri, match['num_games'], format_pct = format_pct)
            for k in range(match['num_games']):
              print_replay(fp, setups[si]['replays'][ri+k])
          else:
            fp.write("    %.2f%%: NO LABEL\n" % (ll*100))
        fp.write("\n")

    with open(full_output_file, 'w') as fp:
      print_full_soln(fp, all_labels)

    with open(single_output_file, 'w') as fp:
      print_single_soln(fp, single_labels)

    print("Wrote label output to %s and %s" % (full_output_file, single_output_file))

    with open(prob_output_file, 'w') as fp:
      print_full_soln(fp, probs_labels, format_pct = True, sort_score = True)

  else:
    usage()
