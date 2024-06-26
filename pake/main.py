
import argparse
import sys
import os
import traceback

import argh

from .registry import Registry
from .rules import FallbackRule
from .exceptions import PakeError
from . import verbose_print

@argh.arg("targets", help="Target names to build. Defaults to the 'default' target.")
@argh.arg("--pakefile", "-f", help="Pakefile filename. Defaults to Pakefile or Pakefile.py")
@argh.arg("--statefile", help="Filepath to store cache state")
@argh.arg("--rebuild", help="Rebuild explicitly listed targets even if we think we don't need to")
@argh.arg("--rebuild-all", help="Rebuild everything (including all dependencies) even if we think we don't need to")
@argh.arg("--graph", help="Instead of building given targets, show a dependency graph")
@argh.arg("--color", action=argparse.BooleanOptionalAction, help="Enable or disable colored output. Default is enabled if stdout is a tty.")
@argh.arg("-q", "--quiet", action="count", default=0, help=" ".join([
	"Specify once to restrict output to errors only. Specify twice to never output anything."
	"Note that even with -qq recipes may run commands that print to stdout."
]))
@argh.arg("-v", "--verbose", action="count", default=0, help=" ".join([
	"Specify multiple times to print additional information:",
	"[1] Print echo() statements and commands run by recipes.",
	"[2] Print when skipping targets due to being up to date.",
	"[3] Print the result (return value or file hash) of each target.",
	"[4] Print each rule considered when matching targets to rules.",
]))
def main(*targets, pakefile=None, statefile=".pake-state", rebuild=False, rebuild_all=False, graph=False, quiet=0, verbose=0, color=None):
	if color is None:
		color = sys.stdout.isatty()
	verbose_print.set(verbose - quiet, color)
	try:
		if rebuild_all:
			rebuild = "deep"
		elif rebuild:
			rebuild = "shallow"
		else:
			rebuild = None

		if pakefile is None:
			candidates = ["Pakefile", "Pakefile.py"]
			for candidate in candidates:
				if os.path.exists(candidate):
					pakefile = candidate
					break
			else:
				raise PakeError("Could not find Pakefile, are you in the right directory?")

		registry = Registry(statefile)
		registry.load_pakefile(pakefile)

		if not targets:
			default_rule, match = registry.resolve("default")
			if isinstance(default_rule, FallbackRule):
				raise PakeError("No targets given and no default target defined.")
			targets = ["default"]

		if graph:
			print_graph(registry.get_deps(*targets))
			return

		for target in targets:
			registry.update(target, rebuild=rebuild)

	except PakeError as e:
		output = str(e)
		if e.__cause__ is not None:
			output += "\n" + "".join(traceback.format_exception(e.__cause__))[:-1]
		verbose_print.verbose_print(-1, verbose_print.color.red(output), file=sys.stderr)
		sys.exit(1)


def print_graph(graph, indent=0):
	for value, children in graph.items():
		# This is an unconditional print (no verbosity) because it was specifically requested to be output
		print("  " * indent + value)
		print_graph(children, indent=indent+1)
