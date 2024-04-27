
import functools
import os
import re
import traceback
from hashlib import sha256
from uuid import uuid4

from .exceptions import BuildError


"""API for declaring dependencies and build rules

Glossary:
	target: A string which identifies a buildable object
	canonical target: A string which uniquely identifies a buildable object.
		Multiple targets may map to the same canonical target, eg. "foo" and "bar/../foo".
	filepath: A canonical target which is a file or directory (ie. not a virtual target)
	rule: A means of building certain matching targets

A generic "rule" consists of a recipe function, wrapped with some metadata:
	PRIORITY: A float which sets the ordering for how rule types are searched.
		This enables eg. simple rules to always overrule pattern rules.
		If rules are tied (eg. within a rule type), priority is by declaration order.
	match(target): Returns non-None if the rule can be used to build the given target.
	deps(match): A list of rules that must be up to date before the matched target can build.
		Must be passed the result of a call to match().
	target(match): Returns the canonical target name of a target previously matched by a call
		to match. Must be passed the result of the match() call.
	needs_update(match, old_result): Returns True if the target needs to be updated even if all deps match
		(eg. because the file has changed on disk). Must be passed the result of the match() call.
		old_result is the cached result that we are potentially invalidating, or None if not cached.
		Note this means we can't distinguish between not being cached and a result of None.
		This isn't a problem in practice as the only rule types that use old_result will
		never return None.
	run(match, deps): Runs the recipe. Assumes all dependencies are already up to date.
		Must be passed the result of a call to match() and a call to deps().
		Normal rules return the hash of the built filepath. Virtual rules may return
		other values, which must be JSONable (lists, dicts, strings, numbers, bools, None)
		and should be small. Dependents will only be re-run if the value has changed.
		So for example:
		- Always returning a constant value (eg. None) indicates this target has no output
		  or its output is always the same, as long as it's up to date. This is suitable
		  for true "phony" targets which you just want to trigger for side effects,
		  and don't actually provide further input to its dependents.
		- If you returned the current date, your dependents would only be cached if their previous
		  build was from the same day.
		The unique() function will give you a value to return that is
		suitable to indicate "my dependents should always update if I have been updated".
	update(match):
		First ensures all its dependencies are up to date
		then calls run() if not up to date and caches it.
		Finally, it returns the result of run(), which may have been cached.
"""


def unique():
	return f"unique:{uuid4()}"


def hash_file(filepath):
	"""Hashes the contents of the given file, returning a string.
	For directories, this is the list of files in that directory.
	"""
	# Note we're intentionally following symlinks here as
	# we are generally interested in file contents.
	# If the user wants to only consider a file changed if the actual symlink pointer changes,
	# they can use a virtual rule that calls readlink.
	if os.path.isdir(filepath):
		hash = sha256("\0".join(sorted(os.listdir(filepath))))
	else:
		hash = sha256()
		with open(filepath, "rb") as f:
			# stream in 64KiB chunks to avoid excessive memory usage
			while True:
				chunk = f.read(64 * 1024)
				if not chunk:
					break
				hash.update(chunk)
	return hash.hexdigest()


def normalize_path(filepath):
	# relpath normalizes components (eg. "foo//bar/.." -> "foo") and leaves us with only two
	# cases: "../PATH" and "PATH".
	path = os.path.relpath(filepath)
	if path.startswith("../"):
		raise ValueError(f"Target cannot be outside current directory: {filepath!r}")
	# We want paths to always have a ./ prefix as this allows us to dismabiguate them from
	# virtual targets.
	return f"./{path}"


class Rule:
	def __init__(self, registry, name):
		self.registry = registry
		self.name = name
		self.registry.register(self)

	def __repr__(self):
		return f"<{type(self).__name__}({self.name!r})>"

	def update(self, match, force=False, _cycle_check=()):
		deps = self.deps(match)
		target = self.target(match)

		if target in _cycle_check:
			cycle = " -> ".join(map(repr, _cycle_check + (target,)))
			raise BuildError(f"Dependency cycle detected: {cycle}")

		inputs = {}
		for dep in deps:
			rule, dep_match = self.registry.resolve(dep)
			# Note we are intentionally not using the canonical target of dep,
			# so that any change in how dep is specified causes a rebuild.
			inputs[dep] = rule.update(dep_match, force=force, _cycle_check = _cycle_check + (target,))

		# Always rebuild if deps have changed, but also ask the rule to do other checks
		# (eg. rebuild if the file hash does not match).
		needs_update = force
		if not needs_update:
			needs_update = self.registry.needs_update(target, inputs)
		if not needs_update:
			result = self.registry.get_result(target)
			needs_update = self.needs_update(match, result)

		if needs_update:
			try:
				result = self.run(match, deps)
			except BuildError:
				raise
			except Exception:
				raise BuildError(
					f"Exception in recipe while building {target!r}:\n{traceback.format_exc().strip()}"
				)
			self.registry.save_result(target, inputs, result)

		return self.registry.get_result(target)


class AlwaysRule(Rule):
	"""A do-nothing rule which always returns a unique string,
	forcing any dependent to always be rebuilt."""
	# Is fundamental and will break things if overriden, always go first
	PRIORITY = float("-inf")

	def __init__(self, registry):
		super().__init__(registry, "always")

	def __repr__(self):
		return "<AlwaysRule>"

	def match(self, target):
		return target if target == "always" else None

	def target(self, match):
		return match

	def deps(self, match):
		return []

	def needs_update(self, match, result):
		return True

	def run(self, match, deps):
		return unique()


class FallbackRule(Rule):
	"""The rule that is used for any filepaths that otherwise don't have a matching rule.
	It returns the hash of the file if it exists, or errors otherwise.
	"""
	# Matches anything, always go last
	PRIORITY = float("inf")

	def __init__(self, registry):
		super().__init__(registry, "fallback")

	def __repr__(self):
		return "<FallbackRule>"

	def match(self, target):
		return normalize_path(target)

	def target(self, match):
		return match

	def deps(self, match):
		return []

	def needs_update(self, match, result):
		# We could hash the file here and compare it, but that's the same thing as running anyway.
		return True

	def run(self, match, deps):
		try:
			return hash_file(match)
		except FileNotFoundError:
			raise BuildError(f"{match} does not exist and there is no rule to create it")


class VirtualRule(Rule):
	"""A rule that doesn't output a file, but rather some other piece of data,
	or nothing. Still obeys the normal behaviour for being considered up-to-date.

	NAME can be used to refer to this rule's target as a dependency.
	If both a virtual target NAME and a file called NAME exist, "NAME" refers to the virtual target
	whereas "./NAME" refers to the file.
	"""
	PRIORITY = 0 # Lower than all file-based rules, to ensure the virtual rule matches NAME first

	def __init__(self, registry, recipe, name=None, deps=[]):
		if name is None:
			name = recipe.__name__
		super().__init__(registry, name)
		self.recipe = recipe
		self._deps = deps

	def match(self, target):
		# Note intentionally not normalizing path, must literally match
		return target if self.name == target else None

	def target(self, match):
		return match

	def deps(self, match):
		return self._deps

	def needs_update(self, match, result):
		return False

	def run(self, match, deps):
		return self.recipe(deps)

	def __call__(self, force=False):
		return self.update(self.match(self.name), force=force)


class FileRule(Rule):
	"""Common base class for rules that build a file."""
	def needs_update(self, match, result):
		try:
			return hash_file(self.target(match)) != result
		except FileNotFoundError:
			return True

	def run(self, match, deps):
		target = self.target(match)
		self.recipe(target, deps)
		try:
			return hash_file(target)
		except FileNotFoundError:
			raise BuildError(f"Recipe for {match} ran successfully but did not create the file")


class TargetRule(FileRule):
	"""Basic rule for a single target filepath.
	Recipe is called with filepath to build and list of deps.
	"""
	PRIORITY = 10 # Prefer simple rules over pattern rules

	def __init__(self, registry, recipe, filepath, deps=[]):
		super().__init__(registry, filepath)
		self.recipe = recipe
		self.filepath = normalize_path(filepath)
		self._deps = deps

	def match(self, target):
		filepath = normalize_path(target)
		return filepath if self.filepath == filepath else None

	def target(self, match):
		return match

	def deps(self, match):
		return self._deps

	def __call__(self, force=False):
		return self.update(self.match(self.filepath), force=force)


class PatternRule(FileRule):
	r"""A rule that builds filepaths matching a regex.
	Deps may contain pattern replacements (eg. "\1.o" where the pattern is ".*\.c").
	Keep in mind that patterns are based on whole filepaths, not just the filename.
	"""
	PRIORITY = 20

	def __init__(self, registry, recipe, pattern, deps=[]):
		super().__init__(registry, pattern)
		self.recipe = recipe
		self.pattern = re.compile(f"^({pattern})$")
		self._deps = deps

	def match(self, target):
		return self.pattern.match(normalize_path(target))

	def target(self, match):
		return match.string

	def deps(self, match):
		return [match.expand(dep) for dep in self._deps]

	def __call__(self, target, force=False):
		match = self.match(target)
		if match is None:
			raise ValueError(f"{target!r} is not a valid target matching {self.name!r}")
		return self.update(match, force=force)


def group(registry, name, deps):
	"""A helper for making a "group" rule, which is a virtual rule which does nothing but
	reference a list of dependencies. Dependents will be rebuilt if any of the dependencies change."""
	def collect_dep_results(deps):
		"""Returns a combination of all dep names and results"""
		return {dep: registry.get_result(dep) for dep in deps}
	return VirtualRule(registry, collect_dep_results, name, deps)


def default(registry, rule):
	"""Helper to create a group rule named "default" that has the given rule as a dependency.
	Intended to be used as a decorator on another rule as an easy way of marking it as the default rule."""
	group(registry, "default", [rule])
	return rule


def always(registry, recipe, name=None, deps=[]):
	"""Helper to create a virtual rule which always runs.
	All this does is automatically add "always" as a dependency. It's just nicer to use,
	especially when it's your only dependency. Compare:
		@virtual(deps=["always"])
	vs
		@always()
	"""
	return VirtualRule(registry, recipe, name, ["always"] + list(deps))


def as_decorator(registry, rule_type):
	"""
	For a given Rule class, creators a decorator-style contructor:
		my_rule = as_decorator(registry, MyRule)

		@my_rule("foo", "bar")
		def f():
			pass
	is equivalent to:
		def f():
			pass
		f = MyRule(registry, f, "foo", "bar")
	"""
	# Ensure the decorator factory inherits the docstring of the rule type
	@functools.wraps(rule_type)
	def decorator_factory(*args, **kwargs):
		def decorator(fn):
			return rule_type(registry, fn, *args, **kwargs)
		return decorator
	return decorator_factory


def with_registry(registry, fn):
	"""Wrap a function. The wrapper automatically provides the registry as the first arg."""
	return functools.wraps(fn)(functools.partial(fn, registry))
