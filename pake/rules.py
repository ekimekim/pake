
import functools
import os
import re
import traceback
from hashlib import sha256
from uuid import uuid4

from .exceptions import BuildError, RuleError


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
		Is passed the result of a call to match() and a dict {dep: result} for each dep returned by deps().
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
	filepath = filepath.encode()
	if os.path.isdir(filepath):
		hash = sha256(b"\0".join(sorted(os.listdir(filepath))))
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
	"""Normalize to ./path, or raise ValueError"""
	if filepath == "":
		raise ValueError("cannot be empty string")
	if "\0" in filepath:
		raise ValueError("may not contain null bytes")
	# relpath normalizes components (eg. "foo//bar/.." -> "foo") and leaves us with only two
	# cases: "../PATH" and "PATH".
	path = os.path.relpath(filepath)
	if path == ".." or path.startswith("../"):
		raise ValueError(f"cannot be outside current directory")
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

	def get_deps(self, match, _target_chain=()):
		"""With the given target match, find all dependencies.
		Returns a tree {dep: (dep's get_deps()}"""
		target = self.target(match)

		has_cycle = target in _target_chain
		_target_chain += (target,)
		if has_cycle:
			raise BuildError(_target_chain, "Dependency cycle detected")

		# This may fail if PatternRule expansions are invalid
		try:
			deps = self.deps(match)
		except Exception as e:
			raise BuildError(_target_chain, "Failed to determine dependencies") from e

		result = {}
		for dep in deps:
			rule, dep_match = self.registry.resolve(dep)
			result[dep] = rule.get_deps(dep_match, _target_chain=_target_chain)

		return result

	def update(self, match, force=False, _target_chain=()):
		"""With the given target match, update this rule and all its dependencies.
		If force given, will re-run even if the cache is valid."""
		target = self.target(match)

		has_cycle = target in _target_chain
		_target_chain += (target,)
		if has_cycle:
			raise BuildError(_target_chain, "Dependency cycle detected")

		# This may fail if PatternRule expansions are invalid
		try:
			deps = self.deps(match)
		except Exception as e:
			raise BuildError(_target_chain, "Failed to determine dependencies") from e

		inputs = {}
		for dep in deps:
			rule, dep_match = self.registry.resolve(dep)
			# Note we are intentionally not using the canonical target of dep,
			# so that any change in how dep is specified causes a rebuild.
			inputs[dep] = rule.update(dep_match, force=force, _target_chain=_target_chain)

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
				result = self.run(match, inputs)
			except RuleError as e:
				raise BuildError(_target_chain, str(e)) from None
			except Exception as e:
				# raise ... from e will include e's traceback in the output
				raise BuildError(_target_chain, "Recipe raised exception") from e
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
	"""The rule that is used for any targets that otherwise don't have a matching rule.
	It returns the hash of the file if it exists, or errors otherwise.
	"""
	# Matches anything, always go last
	PRIORITY = float("inf")

	def __init__(self, registry):
		super().__init__(registry, "fallback")

	def __repr__(self):
		return "<FallbackRule>"

	def match(self, target):
		"""Always matches, but determines if this is a valid filepath first.
		Returns (target, error) where error is None for valid files."""
		try:
			return (normalize_path(target), None)
		except ValueError as e:
			return (target, e)

	def target(self, match):
		target, error = match
		return target

	def deps(self, match):
		return []

	def needs_update(self, match, result):
		# We could hash the file here and compare it, but that's the same thing as running anyway.
		return True

	def run(self, match, deps):
		target, error = match
		if error:
			raise RuleError(f"{target!r} is not a valid filepath ({error}) and no rule by that name exists")
		try:
			return hash_file(target)
		except FileNotFoundError:
			raise RuleError(f"File does not exist and there is no rule to create it")


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
		# Create dir if needed
		dirname = os.path.dirname(target)
		os.makedirs(dirname, exist_ok=True)
		# Dispatch to subclass to run recipe
		self._run(target, deps, match)
		# Hash the resulting file
		try:
			return hash_file(target)
		except FileNotFoundError:
			raise RuleError(f"Recipe ran successfully but did not create the file")


class TargetRule(FileRule):
	"""Basic rule for a single target filepath.
	Recipe is called with filepath to build and list of deps.
	The directory containing the target will be created if it doesn't exist.
	"""
	PRIORITY = 10 # Prefer simple rules over pattern rules

	def __init__(self, registry, recipe, filepath, deps=[]):
		super().__init__(registry, filepath)
		self.recipe = recipe
		try:
			self.filepath = normalize_path(filepath)
		except ValueError as e:
			raise ValueError(f"Invalid filepath for target rule: {e}") from None
		self._deps = deps

	def match(self, target):
		try:
			filepath = normalize_path(target)
		except ValueError:
			return None
		return filepath if self.filepath == filepath else None

	def target(self, match):
		return match

	def deps(self, match):
		return self._deps

	def _run(self, target, deps, match):
		return self.recipe(target, deps)

	def __call__(self, force=False):
		return self.update(self.match(self.filepath), force=force)


class PatternRule(FileRule):
	r"""A rule that builds filepaths matching a regex.
	Deps may contain pattern replacements (eg. "\1.c" where the pattern is ".*\.o").
	Keep in mind that patterns are based on whole filepaths, not just the filename.
	The directory containing the target will be created if it doesn't exist.
	"""
	PRIORITY = 20

	def __init__(self, registry, recipe, pattern, deps=[]):
		super().__init__(registry, pattern)
		self.recipe = recipe
		# Allow leading "./" before match as we're feeding this normalized paths.
		# Make sure to only use non-capture rules to avoid observable changes to group numbering.
		self.pattern = re.compile(rf"^(?:\./)?(?:{pattern})$")
		self._deps = deps

	def match(self, target):
		try:
			filepath = normalize_path(target)
		except ValueError:
			return None
		return self.pattern.match(filepath)

	def target(self, match):
		return match.string

	def deps(self, match):
		return [match.expand(dep) for dep in self._deps]

	def _run(self, target, deps, match):
		return self.recipe(target, deps, match)

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


def alias(registry, name, target):
	"""A helper for making an "alias" rule, which is a virtual rule which does nothing but
	reference a single other rule. It's equivalent to a group rule with one member."""
	return group(registry, name, [target])


def default(registry, rule):
	"""Helper to create a group rule named "default" that has the given rule as a dependency.
	Intended to be used as a decorator on another rule as an easy way of marking it as the default rule.
	The other rule must not be a pattern rule since it doesn't have an unambiguous target.
	"""
	alias(registry, "default", rule.name)
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
