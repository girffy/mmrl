# code for fetching and parsing the data needed for replay labelling
import challonge
import slippi
import pandas as pd
import datetime
import pytz
import calendar
import pickle
import os
import re

import config

# special cases where the regex for a character isn't just their lower case name
char_special_cases = {
  'captain_falcon'            : 'falcon',
  'falco'                     : '(falco(?!n)|bird)',
  'game_and_watch'            : '(game|watch|g&?w)',
  'ice_climbers'              : '(ic(?!h)|climbers|popo|sopo|nana)',
  'dr_mario'                  : '(dr|doc)',
  'donkey_kong'               : '(donkey|kong|dk)',
  'pikachu'                   : 'pika',
  'jigglypuff'                : '(jigg|puff)',
  'young_link'                : '(yo?ung|yl)',
  'link'                      : '(?<!young )link',
  'mario'                     : '(?<!dr\. )mario', # TODO: make this more robust
  'ganondorf'                 : '(gann?on|dorf)',
}

# the slippi.event.CSSCharacter characters that aren't real characters
invalid_chars = ['master_hand', 'wireframe_male', 'wireframe_female',
                 'giga_bowser', 'crazy_hand', 'sandbag', 'popo']

# parse the melee characters from a string
def get_chars(charstr):
  if pd.isnull(charstr):
    return set()

  charstr = charstr.lower()
  chars = set()
  for char in slippi.event.CSSCharacter:
    name = char.name.lower()
    regex = char_special_cases[name] if name in char_special_cases else name
    if name not in invalid_chars and re.search(regex, charstr):
      chars.add(char.name)

  return chars

# given a tag, produce a fingerprint, so that two tags can be compared via
# their fingerprints. Currently this just removes whitespace and lowercases the
# tag. This allows us to more reliably test if two tags are "the same"
def tag_fingerprint(tag):
  return tag.lower().replace(' ', '')

# parse a .csv containing player tags with their mains/secondaries, i.e. parse
# a csv from go/smashers. Expects the 'TAG', 'Main', and 'Secondaries' columns
# to exist, and parses character names from the latter 2 columns
def parse_player_file(fname):
  if fname == None:
    print("No player file specified; not using any player info")
    return {}

  df = pd.read_csv(fname)
  dct = {}
  for _, row in df.iterrows():
    if pd.isnull(row['TAG']):
      continue
    tagfp = tag_fingerprint(row['TAG'])

    if tagfp in dct:
      print("Duplicate tag: '%s'; taking later occurrence" % tagfp)

    dct[tagfp] = get_chars(row['Main']), get_chars(row['Secondaries'])
  print("Parsed player file; %s tags found" % len(dct))
  return dct

# read some tournament brackets from challonge, add some metadata, and output
# them to a JSON file
def fetch_brackets_to_file(challonge_ids, outfile):
  if config.CHALLONGE_USER == None or config.CHALLONGE_API_KEY == None:
    raise Exception("Put your challonge username and api key in config.py")

  all_matches = []
  all_participants = []
  challonge.set_credentials(config.CHALLONGE_USER, config.CHALLONGE_API_KEY)
  for cid in challonge_ids:
    tournament = challonge.tournaments.show(cid)
    matches = challonge.matches.index(cid)
    participants = challonge.participants.index(cid)

    # add some metadata to each challonge match
    for match in matches:
      # TODO: could do some smarter parsing here but oh well, DQ's can be
      # ignored anyway
      if len(match['scores-csv'].split('-')) > 2:
        match['num_games'] = 0
        continue
      scores = list(map(int, match['scores-csv'].split('-')))

      match['player1_score'] = scores[0]
      match['player2_score'] = scores[1]
      match['num_games'] = scores[0] + scores[1]

    all_matches.extend([m for m in matches if m['num_games'] > 0])
    all_participants.extend([p for p in participants if p not in all_participants])

  all_data = {'matches' : all_matches, 'participants' : all_participants}

  with open(outfile, 'wb') as fp:
    #json.dump(all_data, fp, indent=2, sort_keys=True, default=str)
    pickle.dump(all_data, fp)

  print("Finished fetching challonge data; %s matches and %s participants written to %s" %
    (len(all_matches), len(all_participants), outfile))

# read a .slp replay file, extract necessary info into a dict
# TODO: for some reason, py-slippi  throws exceptions for a lot of our replays;
# maybe we should use the JS parser instead?
def parse_slp_file(slp_file, drive):
  try:
    game = slippi.Game(slp_file)
  except Exception as e:
    print("WARNING: slippi parsing exception while reading %s:" % slp_file)
    print("%s: %s" % (type(e), e))
    print("Skipping this replay")
    return None

  #start_date = pytz.timezone(config.TIME_ZONE).localize(game.metadata.date)
  start_time = game.metadata.date.replace(tzinfo = pytz.timezone(config.TIME_ZONE))
  end_time = start_time + datetime.timedelta(seconds = game.metadata.duration / 60.)
  stage = game.start.stage

  ports = []
  numplayers = 0
  for i, port in enumerate(game.frames[-1].ports):
    if port == None:
      ports.append(None)
      continue

    # TODO: more robust win/lose logic, e.g. handle timeouts and LRAstart
    isdead = port.leader.post.stocks == 0
    charname = port.leader.post.character.name

    # address a weird edge case with ICs where popo dies last
    if charname == 'POPO':
      charname = 'ICE_CLIMBERS'

    ports.append({'char' : charname,
                  'dead_at_end' : isdead})
    numplayers += 1

  time_offset = datetime.timedelta(0)
  if drive in config.DRIVE_TIME_OFFSETS:
    time_offset = datetime.timedelta(seconds = config.DRIVE_TIME_OFFSETS[drive])

  dct = {
    'start_time' : start_time - time_offset,
    'end_time'   : end_time - time_offset,
    'filename'   : slp_file,
    'drive'      : drive,
    'ports'      : ports,
    'stage'      : stage.name,
    'numplayers' : numplayers,
  }

  return dct

# given a directory (which should be have a name of the form 'Drive #N'), parse
# all the replays in it and order them by start time
# TODO: it takes quite a while to parse slippi replays; might be better if we
# can parallelize this
def parse_slp_drive(drive_dir):
  print("Parsing replays from directory: %s" % drive_dir)
  replays = []

  # attempt to infer drive name from directory name
  result = re.search('Drive #(\d+)', drive_dir)
  if not result:
    raise Exception("Could not infer drive name from directory: %s" % drive_dir)
  drive = result[0]

  for fname in os.listdir(drive_dir):
    slp_file = os.path.join(drive_dir, fname)
    replay = parse_slp_file(slp_file, drive)
    if replay != None:
      replays.append(replay)
  replays.sort(key = lambda r: r['start_time'])

  setup = {
    'drive' : drive,
    'replays' : replays,
  }

  return setup

# given a directory containing all the drive replay directories, parse each of
# the directories, and write the list of setups to setup_file
def parse_all_slp_drives(drives_dir, setup_file):
  setups = [parse_slp_drive(os.path.join(drives_dir, setup_dir))
            for setup_dir in os.listdir(drives_dir)]

  with open(setup_file, 'wb') as fp:
    #json.dump(setups, fp, indent=2, sort_keys=True, default=str)
    pickle.dump(setups, fp)

  print("Finished parsing slippi data; %s setups with %s total replays written to %s" %
    (len(setups), sum([len(s['replays']) for s in setups]), setup_file))
