#!/usr/bin/python3

from posixpath import basename
import subprocess
import os
import os.path
import argparse
import json
import sys
import tempfile
import time
import multiprocessing
import shutil

VERSION = "v0.2.0"

DEFAULTS = {
    "input_files": None,
    "codec": "libfdk_aac",
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


################################################################################
#  File Processing                                                             #
#  AKA: The meat and potatoes                                                  #
################################################################################


def handle_single(args, output_args={
    "prefix": None,
    "verbose_output": False,
    "dynamic_output": True,
}):
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
                    good_msg("Loaded config.json values", **output_args)
                except Exception as e:
                    fail_msg(
                        "Could not load configuration file, is it corrupt? Continuing with defaults", **output_args)
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
        fail_msg("Could not find any input audio files", **output_args)
        raise Exception("Could not find any input audio files")
    elif len(config["input_files"]) == 1:
        good_msg("Input Audio: {}".format(
            config["input_files"][0]), **output_args)
    else:
        good_msg(
            "Merging Input Audio: {}".format(config["input_files"]), **output_args)

    # Set up the output file in our configuration
    if not config["output_file"]:
        if os.path.isfile(args.output):
            config["output_file"] = args.output
        else:
            out_name = os.path.basename(work_dir) + ".mka"
            config["output_file"] = os.path.join(args.output, out_name)

    good_msg("Output to: {}".format(config["output_file"]), **output_args)

    # Check if the output already exists and the user only wants us handling
    # New files, then return that we skipped this item
    if args.diff and (os.path.exists(config["output_file"]) and not args.update_metadata):
        good_msg("No action required", **output_args)
        return Skipped()

    # Try to locate a cover file
    if not config["cover_file"]:
        for search_item in COVER_SEARCH_ITEMS:
            if os.path.isfile(os.path.join(work_dir, search_item)):
                config["cover_file"] = os.path.join(work_dir, search_item)
                good_msg(
                    "Using cover: {}".format(config["cover_file"]), **output_args)
                break
        else:
            warn_msg("No cover found", **output_args)

    # Try to locate a chapter definition file
    if not config["chapter_file"]:
        for search_item in CHAPTERS_SEARCH_ITEMS:
            if os.path.isfile(os.path.join(work_dir, search_item)):
                config["chapter_file"] = search_item
                good_msg(
                    "Using chapters: {}".format(config["chapter_file"]), **output_args)
                break
        else:
            warn_msg("No chapter info found", **output_args)

    if os.path.exists(config["output_file"]) and args.update_metadata:
        return process_update(work_dir, config, output_args)
    else:
        return process_conversion(work_dir, config, output_args)


def process_update(work_dir, config, output_args):
    # Execute everything in a temporary directory
    with tempfile.TemporaryDirectory(prefix="mkabook") as tmp_dir:
        if config["chapter_file"] is not None:
            try:
                if os.path.splitext(config["chapter_file"])[1] != ".xml":
                    chapters = Chapters(os.path.join(
                        work_dir, config["chapter_file"]))

                    with open(os.path.join(tmp_dir, "chapters.xml"), "w") as chap_file:
                        chapters.write(chap_file, config)
                    config["chapter_file"] = os.path.join(
                        tmp_dir, "chapters.xml")
                else:
                    config["chapter_file"] = os.path.join(
                        work_dir, config["chapter_file"])
            except Exception as e:
                fail_msg("Failed to convert chapters file", **output_args)
                raise Exception("Failed to convert chapter file")
            else:
                good_msg("Converted Chapters file", **output_args)

        # Merge into a temporary file
        merge_options = ["mkvpropedit"]

        something_to_do = False
        if config["chapter_file"]:
            something_to_do = True
            merge_options += ["--chapters", config["chapter_file"]]

        # FIXME: Don't know how to edit cover file information
        # if config["cover_file"]:
        #     something_to_do = True
        #     merge_options += ["--attachment-description",
        #                       "Cover", "--replace-attachment", "name:" + config["cover_file"]]

        # Merge into the existing output file
        merge_options += [config["output_file"]]

        if not something_to_do:
            return Skipped()

        poll_process("Updating MKV Meta-data", merge_options,
                     **output_args)

        #shutil.move(os.path.join(tmp_dir, "tmp.mka"), config["output_file"])

    return Updated(config["chapter_file"] is not None, config["cover_file"] is not None, False)


def process_conversion(work_dir, config, output_args):
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
                else:
                    config["chapter_file"] = os.path.join(
                        work_dir, config["chapter_file"])
            except Exception as e:
                fail_msg("Failed to convert chapters file", **output_args)
                raise Exception("Failed to convert chapter file")
            else:
                good_msg("Converted Chapters file", **output_args)

        if len(config["input_files"]) > 1:
            # Merge files
            sorted_input = sorted(config["input_files"])

            with open(os.path.join(tmp_dir, "concat.txt"), 'w') as c:
                for input in sorted_input:
                    c.write("file " + "'" + input + "'\n")

            # p = subprocess.Popen(["ffmpeg", "-f", "concat", "-safe", "0", "-i", os.path.join(
            #     tmp_dir, "concat.txt"), "-c", "copy", os.path.join(tmp_dir, "concat.mka")], **REDIRECT_ARGS)
            # poll_message(p, prefix + "Merging audio tracks")
            poll_process("Merging audio tracks", ["ffmpeg", "-f", "concat", "-safe", "0", "-i", os.path.join(
                tmp_dir, "concat.txt"), "-c", "copy", os.path.join(tmp_dir, "concat.mka")], **output_args)

            config["input_file"] = os.path.join(tmp_dir, "concat.mka")
        else:
            config["input_file"] = config["input_files"][0]

        # p = subprocess.Popen(["ffmpeg", "-i", config["input_file"], "-acodec",
        #                       config["codec"], os.path.join(tmp_dir, "converted.mka")], **REDIRECT_ARGS)
        # poll_message(p, prefix + "Converting Audio")

        poll_process("Converting Audio", ["ffmpeg", "-i", config["input_file"], "-acodec",
                                          config["codec"], os.path.join(tmp_dir, "converted.mka")], **output_args)

        merge_options = ["mkvmerge", "-o", config["output_file"]]

        if config["chapter_file"]:
            merge_options += ["--chapters", config["chapter_file"]]

        if config["cover_file"]:
            merge_options += ["--attachment-description",
                              "Cover", "--attach-file", config["cover_file"]]

        # Input is our temp file
        merge_options += [os.path.join(tmp_dir, "converted.mka")]

        # p = subprocess.Popen(
        #     merge_options, **REDIRECT_ARGS)

        # poll_message(p, prefix + "Merging MKV Meta-data")
        poll_process("Merging MKV Meta-data", merge_options,
                     **output_args)

    return Converted(config["codec"], config["chapter_file"] is not None, config["cover_file"] is not None, False)


class ConversionResponse:
    pass


class Converted:
    def __init__(self, codec, has_chapters, has_cover, has_info):
        self.codec = codec
        self.has_chapters = has_chapters
        self.has_cover = has_cover
        self.has_info = has_info

    def __str__(self):
        return "Converted\n\tCodec: {}\n\tChapters: {}\n\tCover: {}\n\tTags: {}".format(self.codec, self.has_chapters, self.has_cover, self.has_info)


class Skipped(ConversionResponse):
    pass

    def __str__(self):
        return "Already exits, nothing to do"


class Updated:
    def __init__(self, has_chapters, has_cover, has_info):
        self.has_chapters = has_chapters
        self.has_cover = has_cover
        self.has_info = has_info

    def __str__(self):
        return "Updated\n\tChapters: {}\n\tCover: {}\n\tTags: {}".format(self.has_chapters, self.has_cover, self.has_info)

################################################################################
#  Arguments                                                                   #
################################################################################


def parse_args():
    parser = argparse.ArgumentParser(
        description="A tool for creating MKV based audiobooks: {}".format(VERSION))

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
                        help="Don't reconvert the audio, only update the available meta-data [Under construction: Only updates chapter info at this time]")
    parser.add_argument("-d", "--diff", action="store_true",
                        help="Only process if an existing file isn't in the specified output location. Useful when batch processing.")
    parser.add_argument("--batch", action="store_true",
                        help="Scan the input directory and treat each sub-directory as a single item.")
    parser.add_argument("-j", "--jobs", type=int, default=1,
                        help="How many items to batch process at once")

    return parser.parse_args()


################################################################################
#  Batch Processing                                                            #
################################################################################

def shim(input):
    (args, entry) = input

    # Change the item we are looking at
    args.INPUT_FILE_OR_DIR = entry

    try:
        return handle_single(args, output_args={
            "verbose_output": False,
            "dynamic_output": False,
            "prefix": os.path.basename(entry)
        })
    except Exception as e:
        return e


def handle_batch(args):
    if not os.path.isdir(args.INPUT_FILE_OR_DIR):
        fail_msg("Input expected to be a directory")
        sys.exit(1)

    # Find all sub-directories of the input
    to_process = []
    for item in os.listdir(args.INPUT_FILE_OR_DIR):
        entry = os.path.join(args.INPUT_FILE_OR_DIR, item)
        if os.path.isdir(entry):
            to_process.append((args, entry))

    good_msg("Found {} items to process: {}".format(
        len(to_process), "\n\t" + "\n\t".join([os.path.basename(x[1]) for x in to_process])))
    progress_msg("Running with {} Job(s)".format(args.jobs))

    # Process the items
    pool = multiprocessing.Pool(args.jobs)

    # Figure out which items succeeded and which failed
    output = pool.map(shim, to_process)
    potential_errors = zip(to_process, output)

    # Give use a nice summary of the processed items
    print("\n" + bcolors.HEADER + "Conversion Summary" + bcolors.ENDC)
    err_count = 0
    for ((_, name), ret) in potential_errors:
        if type(ret) is Exception:
            fail_msg("An Error Ocurred" + "\n\t{}".format(ret),
                     prefix=os.path.basename(name))
            err_count += 1
        else:
            good_msg(ret, prefix=os.path.basename(name))

    print("")
    if err_count > 0:
        fail_msg("Encountered {} errors while processing".format(err_count))
    else:
        good_msg("Batch Completed Successfully")


################################################################################
#  Subprocess Handling                                                         #
################################################################################

def poll_process(msg, args, verbose_output=False, dynamic_output=True, prefix=None, poll_time=0.25):
    raw_msg = msg
    # Useful constant
    POLL_STAGES = ["▖", "▘", "▝", "▗"]
    # Build args for redirecting output if we don't want verbose output
    REDIRECT = {}
    if not verbose_output:
        REDIRECT = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
    else:
        # Verbose ouptut implies no dynamic messages
        dynamic_output = False

    # Build the full message if we have a prefix
    if prefix:
        msg = "{}: {}".format(prefix, msg)

    if not dynamic_output:
        progress_msg(msg)

    # Spawn the process
    proc = subprocess.Popen(args, **REDIRECT)

    # Wait for the process to terminate, maybe updating the spinner
    if not dynamic_output:
        proc.wait()
    else:
        poll_step = 0
        while True:
            print("[" + bcolors.OKCYAN + POLL_STAGES[poll_step %
                                                     4] + bcolors.ENDC + "] {}".format(msg), end="\r")

            time.sleep(poll_time)
            poll_step += 1

            if proc.poll() is not None:
                break

    # Check what happened to the sub-process and print that out
    if proc.returncode == 0:
        good_msg(msg)
    else:
        fail_msg(msg)
        # Dump the captured output if we weren't already in verbose mode
        if not verbose_output:
            for line in proc.stderr.readlines():
                print("\t" + line.decode("utf-8"), file=sys.stderr, end="")
        # Exit
        raise Exception("Subprocess returned error while: {}".format(raw_msg))

################################################################################
#  Debug Messages                                                              #
################################################################################


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


# Print that we started an item to the console
def progress_msg(msg, prefix=None, **kwargs):
    START = bcolors.OKCYAN + "=" + bcolors.ENDC
    if prefix is not None:
        print("[" + START + "] {}: {}".format(prefix, msg))
    else:
        print("[" + START + "] {}".format(msg))


# Print a success message to the console
def good_msg(msg, prefix=None, **kwargs):
    GOOD = bcolors.OKGREEN + "✓" + bcolors.ENDC

    if prefix is not None:
        print("[" + GOOD + "] {}: {}".format(prefix, msg))
    else:
        print("[" + GOOD + "] {}".format(msg))

# Print a message indicating failure to a file (default stderr)


def fail_msg(msg, file=sys.stderr, prefix=None, **kwargs):
    FAIL = bcolors.FAIL + "🗴" + bcolors.ENDC

    if prefix is not None:
        print("[" + FAIL + "] {}: {}".format(prefix, msg), file=file)
    else:
        print("[" + FAIL + "] {}".format(msg), file=file)


# Print a warning message to the console
def warn_msg(msg, prefix=None, **kwargs):
    WARN = bcolors.WARNING + "?" + bcolors.ENDC

    if prefix is not None:
        print("[" + WARN + "] {}: {}".format(prefix, msg))
    else:
        print("[" + WARN + "] {}".format(msg))


################################################################################
#  Text Chapter Parsing                                                        #
################################################################################

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


################################################################################
#  Main                                                                        #
################################################################################

def main():
    args = parse_args()

    if args.batch:
        handle_batch(args)
    else:
        try:
            handle_single(args, output_args={
                "prefix": None,
                "verbose_output": args.v,
                "dynamic_output": True
            })
        except Exception as e:
            print(e)
            sys.exit(1)


if __name__ == "__main__":
    main()
