#!/usr/bin/python3

import subprocess
import os
import os.path
import argparse
import json
import sys
import tempfile
import time
import multiprocessing

DEFAULTS = {
    "input_files": None,
    "codec": "aac",  # "libfdk_aac",
    "bitrate": None,
    "cover_file": None,
    "chapter_file": None,
    "convert_text_chapters": True,
    "use_sub_chapters": False,
    "output_file": None,
}

AUDIO_EXTENSION = [
    ".mka", ".m4a", ".m4b", ".flac", ".ogg", ".mp3"
]

COVER_SEARCH_ITEMS = ["cover.jpg", "cover.jpeg", "cover.png"]
CHAPTERS_SEARCH_ITEMS = ["chapters.xml",
                         "chapter.xml", "chapters.txt", "chapter.txt"]


# Parse QT Style chapters
class Chapters:
    XML_HEADER = """<?xml version="1.0"?>
<!-- <!DOCTYPE Chapters SYSTEM "matroskachapters.dtd"> -->
<Chapters>
    <EditionEntry>
"""

    XML_FOOTER = """    </EditionEntry>
</Chapters>
"""

    def __init__(self, chapter_file):
        self.chapters = []
        chap_stack = []
        with open(chapter_file, 'r') as input:
            # Each line should be a chapter => ChapterAtom
            for line in input.readlines():
                [start, title] = line.split(" ", 1)
                new_chap = Chapter(start, title)

                # Find next level
                title = title.replace("    ", "\t")
                indent_level = title.count('\t')

                if indent_level < len(chap_stack):
                    while len(chap_stack) != indent_level:
                        chap_stack.pop()

                if len(chap_stack) != 0:
                    chap_stack[-1].add_child(new_chap)
                else:
                    self.chapters.append(new_chap)

                chap_stack.append(new_chap)

    def write(self, out_file, config):
        out_file.write(Chapters.XML_HEADER)
        for chapter in self.chapters:
            chapter.write(out_file, 0, config)

        out_file.write(Chapters.XML_FOOTER)


class Chapter:
    XML_ENTRY_FORMAT = """      <ChapterAtom>
            <ChapterTimeStart>{}</ChapterTimeStart>
            <ChapterDisplay>
                <ChapterString>{}</ChapterString>
                <ChapterLanguage>eng</ChapterLanguage>
            </ChapterDisplay>
"""
    XML_ENTRY_CLOSE = """       </ChapterAtom>
"""

    def __init__(self, start, title):
        self.start = start
        self.title = title.strip()
        self.children = []
        pass

    def add_child(self, child):
        self.children.append(child)

    def write(self, file, sub_level, config):
        if config['use_sub_chapters']:
            file.write(Chapter.XML_ENTRY_FORMAT.format(self.start, self.title))
            for child in self.children:
                child.write(file, sub_level + 1, config)
            file.write(Chapter.XML_ENTRY_CLOSE)
        else:
            file.write(Chapter.XML_ENTRY_FORMAT.format(
                self.start, '\t'*sub_level + self.title))
            file.write(Chapter.XML_ENTRY_CLOSE)
            for child in self.children:
                child.write(file, sub_level + 1, config)


USE_POLL_SPINNER = True
REDIRECT_ARGS = {
    'stdout': subprocess.PIPE,
    'stderr': subprocess.PIPE,
}


def handle_single(args, prefix=""):
    # Copy in the default configs
    config = DEFAULTS.copy()

    # THe working directory is where we will search for all our input
    # The user may have specified a single file, so just use its location as
    # working directory
    work_dir = args.INPUT_FILE_OR_DIR
    if not os.path.isdir(args.INPUT_FILE_OR_DIR):
        work_dir = os.path.dirname(args.INPUT_FILE_OR_DIR)
        # Set this as the only input file
        config["input_files"] = [args.INPUT_FILE_OR_DIR]

    work_dir = os.path.abspath(work_dir)

    # Load up the config.json from this directory
    if not args.ignore_cfg:
        try:
            with open(os.path.join(work_dir, "config.json"), 'r') as cfg_file:
                try:
                    config_from_file = json.load(cfg_file)
                    config.update(config_from_file)
                    good_msg(prefix + "Loaded config.json values")
                except Exception as e:
                    fail_msg(
                        prefix + "Could not load configuration file, is it corrupt? Continuing with defaults")
        except Exception as e:
            pass

    # Command line items superceed even the config file
    if args.codec is not None:
        config["codec"] = args.codec
    if args.cover is not None:
        config["cover_file"] = args.cover
    if args.chapters is not None:
        config["chapter_file"] = args.chapters
    config["use_sub_chapters"] = args.use_sub_chapters

    # Try to locate input files
    if not config["input_files"]:
        config["input_files"] = []
        for item in os.listdir(work_dir):
            (_, ext) = os.path.splitext(item)

            if ext in AUDIO_EXTENSION:
                config["input_files"].append(os.path.join(work_dir, item))

    if len(config["input_files"]) == 0:
        fail_msg(prefix + "Could not find any input audio files")
        sys.exit(1)
    elif len(config["input_files"]) == 1:
        good_msg(prefix + "Input Audio: {}".format(config["input_files"][0]))
    else:
        good_msg(
            prefix + "Merging Input Audio: {}".format(config["input_files"]))

    # Try to locate a cover file
    if not config["cover_file"]:
        for search_item in COVER_SEARCH_ITEMS:
            if os.path.isfile(os.path.join(work_dir, search_item)):
                config["cover_file"] = os.path.join(work_dir, search_item)
                good_msg(
                    prefix + "Using cover: {}".format(config["cover_file"]))
                break
        else:
            warn_msg(prefix + "No cover found")

    # Try to locate a chapter definition file
    if not config["chapter_file"]:
        for search_item in CHAPTERS_SEARCH_ITEMS:
            if os.path.isfile(os.path.join(work_dir, search_item)):
                config["chapter_file"] = search_item
                good_msg(
                    prefix + "Using chapters: {}".format(config["chapter_file"]))
                break
        else:
            warn_msg(prefix + "No chapter info found")

    # Set up the output file in our configuration
    if not config["output_file"]:
        if os.path.isfile(args.output):
            config["output_file"] = args.output
        else:
            out_name = os.path.basename(work_dir) + ".mka"
            config["output_file"] = os.path.join(args.output, out_name)

    good_msg(prefix + "Output to: {}".format(config["output_file"]))

    # Exectute everything within the context of the Temporary directory
    with tempfile.TemporaryDirectory(prefix="mkabook") as tmp_dir:
        # Check if the chapters need conversion and create a temp xml file

        if config["chapter_file"] is not None:
            try:
                if os.path.splitext(config["chapter_file"])[1] != ".xml":
                    chapters = Chapters(os.path.join(
                        work_dir, config["chapter_file"]))

                    with open(os.path.join(tmp_dir, "chapters.xml"), "w") as chap_file:
                        chapters.write(chap_file, config)
                    config["chapter_file"] = os.path.join(
                        tmp_dir, "chapters.xml")
            except Exception as e:
                fail_msg(prefix + "Failed to convert chapters file")
                sys.exit(1)
            else:
                good_msg(prefix + "Converted Chapters file")

        if len(config["input_files"]) > 1:
            # Merge files
            sorted_input = sorted(config["input_files"])

            with open(os.path.join(tmp_dir, "concat.txt"), 'w') as c:
                for input in sorted_input:
                    c.write("file " + "'" + input + "'\n")

            p = subprocess.Popen(["ffmpeg", "-f", "concat", "-safe", "0", "-i", os.path.join(
                tmp_dir, "concat.txt"), "-c", "copy", os.path.join(tmp_dir, "concat.mka")], **REDIRECT_ARGS)
            poll_message(p, prefix + "Merging audio tracks")

            config["input_file"] = os.path.join(tmp_dir, "concat.mka")
        else:
            config["input_file"] = config["input_files"][0]

        p = subprocess.Popen(["ffmpeg", "-i", config["input_file"], "-acodec",
                              config["codec"], os.path.join(tmp_dir, "converted.mka")], **REDIRECT_ARGS)
        poll_message(p, prefix + "Converting Audio")

        merge_options = ["mkvmerge", "-o", config["output_file"]]

        if config["chapter_file"]:
            merge_options += ["--chapters", config["chapter_file"]]

        if config["cover_file"]:
            merge_options += ["--attachment-description",
                              "Cover", "--attach-file", config["cover_file"]]

        # Input is our temp file
        merge_options += [os.path.join(tmp_dir, "converted.mka")]

        p = subprocess.Popen(
            merge_options, **REDIRECT_ARGS)

        poll_message(p, prefix + "Merging MKV Meta-data")


def parse_args():
    parser = argparse.ArgumentParser(
        description="A tool for creating MKV based audiobooks")

    parser.add_argument("INPUT_FILE_OR_DIR")
    parser.add_argument(
        "--codec", choices=["libfdk_aac", "aac", "flac", "mp3", "copy"], help="Set the codec to use for the audio track. 'copy' will perorm no conversion. [Default: libfdk_aac]")
    parser.add_argument(
        "-i", "--ignore-cfg", action='store_true', help="If set mkabook will ignore values set in cfg.json and use defaults or command line values")
    parser.add_argument("--cover", type=str,
                        help="Set the name of the cover image to look for")
    parser.add_argument("--chapters", type=str,
                        help="Specify the chapters file to use, if this file is a txt file in the QT format it will be converted")
    parser.add_argument(
        "--use-sub-chapters", action="store_true", default=False, help="If set, when converting from Text chapters to XML, the tool will output sub-chapters instead of indented titles")

    parser.add_argument("-o", "--output", type=str, default=".",
                        help="Output directory or filename. If a directory is provided the output will be the name of the input file directory with the .mka extension")
    parser.add_argument("-v", action="store_true",
                        help="Verbose program output")
    parser.add_argument("-u", "--update-metadata", action="store_true",
                        help="Don't reconvert the audio, only update the available meta-data [Unimplemented]")
    parser.add_argument("--batch", action="store_true",
                        help="Scan the input directory and treat each sub-directory as a single item.")
    parser.add_argument("-j", "--jobs", type=int, default=1,
                        help="How many items to batch process at once")

    return parser.parse_args()


def shim(input):
    (args, entry) = input

    # Change the item we are looking at
    args.INPUT_FILE_OR_DIR = entry

    try:
        handle_single(args, prefix=os.path.basename(entry) + ": ")
        return 0
    except Exception as e:
        print(os.path.basename(entry) +
              ": Encountered and Exception: {}".format(e))
        return 1


def handle_batch(args):

    if not os.path.isdir(args.INPUT_FILE_OR_DIR):
        fail_msg("Input expected to be a directory")
        sys.exit(1)

    to_process = []
    for item in os.listdir(args.INPUT_FILE_OR_DIR):
        entry = os.path.join(args.INPUT_FILE_OR_DIR, item)
        if os.path.isdir(entry):
            to_process.append((args, entry))

    good_msg("Found {} items to process: {}".format(
        len(to_process), to_process))
    print("Running {} Job(s)".format(args.jobs))

    pool = multiprocessing.Pool(args.jobs)

    output = pool.map(shim, to_process)


def main():
    global USE_POLL_SPINNER
    global REDIRECT_ARGS
    args = parse_args()

    # Switch to verbose output and don't redirect the sub command output

    if args.batch:
        USE_POLL_SPINNER = False
        handle_batch(args)
    else:
        # Verbose only makes sense for single mode for now
        if args.v:
            USE_POLL_SPINNER = False
            REDIRECT_ARGS = {}
        handle_single(args)


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def good_msg(msg):
    GOOD = bcolors.OKGREEN + "✓" + bcolors.ENDC
    print("[" + GOOD + "] {}".format(msg))


def fail_msg(msg):
    FAIL = bcolors.FAIL + "🗴" + bcolors.ENDC
    print("[" + FAIL + "] {}".format(msg))


def warn_msg(msg):
    WARN = bcolors.WARNING + "?" + bcolors.ENDC
    print("[" + WARN + "] {}".format(msg))


def poll_message(proc, msg, poll_time=0.1):
    # Useful constants
    GOOD = bcolors.OKGREEN + "✓" + bcolors.ENDC
    FAIL = bcolors.FAIL + "🗴" + bcolors.ENDC
    POLL_STAGES = ["▖", "▘", "▝", "▗"]

    if not USE_POLL_SPINNER:
        proc.wait()
    else:
        poll_step = 0
        while True:
            print("[" + POLL_STAGES[poll_step %
                                    4] + "] {}".format(msg), end="\r")
            time.sleep(poll_time)
            poll_step += 1

            if proc.poll() is not None:
                break

    if proc.returncode == 0:
        print("[" + GOOD + "] {}".format(msg))
    else:
        print("[" + FAIL + "] {}".format(msg))
        #print(msg, file=sys.stderr)
        if proc.stderr is not None:
            for line in proc.stderr.readlines():
                print("\t" + line.decode('utf-8'), file=sys.stderr, end="")
        sys.exit(1)


if __name__ == "__main__":
    main()
