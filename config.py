# the challonge user and API key to use with the challonge API
CHALLONGE_USER = None
CHALLONGE_API_KEY =  None


# the parameters for the gaussian distributions of how time differences (in
# seconds) are distributed. ANNOUNCE_TO_START is for challonge start time to
# 1st replay start time, and END_TO_REPORT is for last replay end time to
# challonge end time
ANNOUNCE_TO_START_MEAN = 60.0
ANNOUNCE_TO_START_SD = 180.0
END_TO_REPORT_MEAN = 30.0
END_TO_REPORT_SD = 180.0

# it is assumed that the first game never starts before the challonge start
# time, and the last game never ends before the challonge end time; however
# they are allowed to break these rules by at most TIME_SLACK seconds, to
# account for i.e. slight offsets in timing
TIME_SLACK = 300.0

# required number of players for the replay files; should be 2 for singles, 4
# for doubles
REQ_NUM_PLAYERS = 2

# an estimate of how likely someone is to have randomly picked the right
# character for a replay, knowing nothing about the character or their
# mains/secondaries; derived from some data about character distribution
DEFAULT_PROB = 0.057028

# probability of someone choosing one of their mains
MAIN_CHAR_PROB = 0.8

# probability of someone choosing one of their secondaries
SEC_CHAR_PROB = 0.1

# a dict supplying time offsets for each drive; each drive has the specified
# number of seconds subtracted from each of its timestamps. Strings should
# match the name of the folder for the drive's replay files
# TODO: the usb drives sometimes float from wii to wii, messing up these
# offsets. I think the .slp files have some kind of identifier for the wii, but
# py-slippi doesn't pick it up; could maybe fix this by switching to the js
# slippi parser
DRIVE_TIME_OFFSETS = {
  'Drive #2' : 60,
  'Drive #3' : 2592093,
  'Drive #5' : 60,
  'Drive #7' : 60,
  'Drive #8' : 60, # lol
}

# the time zone of the replays; slippi replay timestamps are assumed to be in
# this timezone, and final output is displayed in this timezone. Should be
# understandable by pytz.timezone()
TIME_ZONE = 'America/Los_Angeles'

# objective value of leaving a match unlabelled. Acts as a threshold; labels
# below this score will not be used, and matches that cannot score higher than
# this will end up unlabelled
NOLABEL_OBJVAL = -25.0


# file locations, relative to the output_dir from the command line invocation
CHALLONGE_FILE = 'challonge_data.p' # file containing bracket match data
SLIPPI_FILE = 'slippi_data.p' # file containing parsed replay data
FULL_OUTPUT_FILE = 'full_output.txt' # file containing all feasible label scores
SINGLE_OUTPUT_FILE = 'single_output.txt' # file containing LP solution output
