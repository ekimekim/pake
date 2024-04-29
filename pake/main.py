
import sys
import os
import traceback

import argh

from .registry import Registry
from .rules import FallbackRule
from .exceptions import PakeError

@argh.arg("targets", help="Target names to build. Defaults to the 'default' target.")
@argh.arg("--pakefile", "-f", help="Pakefile filename. Defaults to Pakefile or Pakefile.py")
@argh.arg("--statefile", help="Filepath to store cache state")
@argh.arg("--force", help="Rebuild everything even if we think we don't need to")
@argh.arg("--graph", help="Instead of building given targets, show a dependency graph")
def main(*targets, pakefile=None, statefile=".pake-state", force=False, graph=False):
	try:
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
			registry.update(target, force=force)

	except PakeError as e:
		print(e, file=sys.stderr)
		if e.__cause__ is not None:
			traceback.print_exception(e.__cause__)
		sys.exit(1)


def print_graph(graph, indent=0):
	for value, children in graph.items():
		print("  " * indent + value)
		print_graph(children, indent=indent+1)
