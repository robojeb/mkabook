# What is this?

`mkabook` is a tool for managing a library of audio-books as `.mka` Matroska 
Audio files. This tool allows merging multiple files into one single audio-book, 
adding Chapter information, and adding a Cover image. 

This is intended as a replacement for `m4b-tools`. I found that `.m4b` files 
are a pain to work with. They have limited codec options and the tooling around 
them is complicated. For example I found it incredibly difficult to create chapter
titles with indentation using `m4b-tools`, this is useful for me because I have
several audiobooks which are collections of shorter stories with their own sub-chapters. 
`mkabook` easily allows creating chapters with indentation, and can direclty use
the `.mkv` sub-chapter format (if your player supports that data). 

There are two modes of operation, single file and batch mode. 
Single file will operate on one input and output at a time. 
Batch mode allows processing mulitple input files at once, and includes the ability
to skip items which have been processed in a previous invocation. 

# Features
- Automatic detection of common input files from directories
- Automatically merge multiple files into a single audio-book
- Automatically convert chapters from QuickTime format to Matroksa XML Format
    - Handling for sub-chapters
- Multiple codec formats
    - ACC (Including LibFDK_ACC in the docker image)
    - FLAC
    - MP3
    - Codec passthrough
- Batch processing
    - Automatic "diff" mode doesn't convert files that already exist in the output directory
- Storing per-input configurations on the filesystem for easier batch processing

# TODO Features
- Automatic application of Meta-data Tags from an easy format
- Finished meta-data update functionality
- Chapter extraction(?)
   - Auto generated chapters from merging files
- File splitting(?)
- Ability to update existing files with `--diff` when input audio files have been removed

# Installation

For the best experience I recommend running the tool inside a docker container. 
This allows easier use of the `libfdk_aac` codec which in my experience produces
better output than the standard `ffmpeg` `aac` codec. 

To build the docker, make sure you have docker installed and run:

```
git clone https://github.com/robojeb/mkabook.git
cd mkabook
docker build . -t mkabook
```

Then either add the `mkabook` script from this repo to your path, or add the 
following alias to your shell: 

```
alias mkabook='docker run -it --rm -u $(id -u):$(id -g) -v "$(pwd)":/mnt mkabook
```

NOTE: Because of the sandboxing provided by docker, all input and output files 
must exist below the directory you execute this command from. 

## I'd rather use the python file directly

That is fine you can invoke `mkabook.py` directly and add it to your path. 
I would recommend the following change: 

```diff

DEFAULTS = {
    "input_files": None,
-   "codec": "libfdk_aac",
+   "codec": "aac",
    "bitrate": None,
    "cover_file": None,
    "chapter_file": None,
    "convert_text_chapters": True,
    "use_sub_chapters": False,
    "output_file": None,
}
```

Most operating systems don't ship an `ffmpeg` with `libfdk_aac` by default and
you will get conversion errors if you keep this default. 
You could also always remember to provide the `--codec` option or configure a 
`config.json` file for all of your files, but that is prone to error. 

# Common Tasks

## Keeping a library up-to-date with batch processing

For best results batch processing I recommend the following directory structure:
```
<audio-book folder>
├- input
|   ├- Some Book Title
|   |     └ <input files>
|   └- Other Book Title
|         └ <input files>
| 
└- output
```

Then while in the `<audio-book folder>` you can execute the following: 

```
mkatool -o ./output --batch --diff ./input
```

The tool will automatically detect the input directories and process the files. 
This will result in the output `Some Book Title.mka` and `Other Book Title.mka` 
in the `Output` folder. 

Subsequent invocations will skip producing those files again as long as they
exist in the `output` directory because of the `--diff` option.

## Making sure batch processing uses the right codec for each of my files

Its often useful to configure a codec to be used for each of your files. 
For example, if the input is already a nicely encoded `acc` file or a lossless 
`flac` you might prefer to just `copy` that directly into the output audio-book. 

Because the `--codec` command applies to all inputs during batch processing `mkabook`
allows creation of a `config.json` file to set local properties. 
This file can be placed in the input directory and will override defaults, but will
be overriden by values provided on the command line. 
Valid entries are: 

```js
{
    "codec": "libfdk_acc", // Also valid ["acc", "flac", "mp3", "copy"]
    "chapter_file": "chapters.xml", // Specify the name of the chapter file
    "cover_file": "cover.jpg", // Specify the name of the cover image
}
```
