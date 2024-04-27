
"""This example builds a typical C project:
- All .c files build into .o files
- The .o files are then built into an executable, with both debug and release profiles
- In this simple case, all .h files are assumed to be used by all .c files,
  any .h file change invalidates all .c files.
"""

import re

NAME = "myproject"
cc = cmd("gcc", "-Wall")

PROFILES = {
	"debug": cc("-Og", "-g"),
	"release": cc("-O3"),
}

headers = match_files(r".*\.h")
sources = match_files(r".*\.c")
objects = [re.sub(source, r"\.c$", r"\.o") for source in sources]

@pattern(r"build/(debug|release)/(.*)\.o", deps=["\2.c", headers])
def build_object(target, deps, match):
	profile = match.group(1)
	source, *headers = deps
	cc = PROFILES[profile]
	cc("-c", "-o", target, source)

@pattern(f"build/(debug|release)/{NAME}", deps=[rf"build/\1/{o}" for o in objects])
def build(target, deps, match):
	profile = match.group(1)
	cc = PROFILES[profile]
	cc("-o", target, *deps)


alias("default", f"build/release/{NAME}")
alias("debug", f"build/debug/{NAME}")
