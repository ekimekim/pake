
import fcntl
import json
import logging
import os
from bisect import insort_right
from uuid import uuid4

from . import rules, cmd
from .exceptions import PakeError


class State:
	"""Encapsulates a JSON value which is saved to file.
	The file path uses a file lock to ensure only one pake instance is using it.
	The data is available under state.data, and should be modified in place and then
	save() called.
	"""
	def __init__(self, path):
		self.path = path

		while True:
			# Open or create file. We create it if it doesn't exist so that we can take the lock.
			# We keep it open to hold the lock.
			self.file = open(self.path, "a+")
			# Obtain lock on file, preventing simultaneous usage.
			# Unlocking is implicit when the file is later closed.
			try:
				fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
			except BlockingIOError:
				raise PakeError(f"The state file {self.path!r} is locked - is another instance of pake running?") from None
			# There is a race condition where we open a file, it gets overwritten with a new version,
			# and then the old version's lock is released so our lock succeeds.
			# We detect this condition by re-checking the filepath still refers to the same file.
			old_stat = os.fstat(self.file.fileno())
			new_stat = os.stat(self.path)
			# (st_dev, st_ino) uniquely identifies a file
			if (old_stat.st_dev, old_stat.st_ino) != (new_stat.st_dev, new_stat.st_ino):
				logging.warning(f"State file {self.path!r} changed between open and lock, retrying")
				self.file.close()
				continue # retry
			break # success

		self.file.seek(0)
		content = self.file.read()
		if content == "":
			# file was newly created
			self.data = {}
		else:
			self.data = json.loads(content)

	def save(self):
		# To prevent partial writes, write to a tempfile then replace the state file with it.
		# Note we lock the new file BEFORE renaming it, to prevent a race where another
		# pake instance opens and locks it before we can.
		temp_path = f"{self.path}.{uuid4()}.tmp"
		new_file = open(temp_path, "w")
		new_file.write(json.dumps(self.data) + "\n")
		new_file.flush()
		fcntl.flock(new_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
		os.rename(temp_path, self.path)
		# Since our old file is now un-openable, we can safely release the lock.
		# It's possible someone is still holding onto it from before the rename,
		# but they'll check it again after locking and realise the file is different.
		self.file.close()
		# keep the lock on the new file by keeping it open
		self.file = new_file


class Registry:
	"""A registry holds rule definitions and the state needed to know
	what targets need building. Generally there is only one registry.
	"""
	def __init__(self, state_path):
		self.state = State(state_path)
		# Initial implicit rules
		self.rules = []
		rules.AlwaysRule(self)
		rules.FallbackRule(self)

	def load_pakefile(self, pakefile):
		injected = {
			"registry": self,
			"virtual": rules.as_decorator(self, rules.VirtualRule),
			"target": rules.as_decorator(self, rules.TargetRule),
			"pattern": rules.as_decorator(self, rules.PatternRule),
			"always": rules.as_decorator(self, rules.always),
			"group": rules.with_registry(self, rules.group),
			"alias": rules.with_registry(self, rules.alias),
			"default": rules.with_registry(self, rules.default),
			"cmd": cmd.cmd,
			"sudo": cmd.sudo,
			"run": cmd.run,
			"shell": cmd.shell,
			"find": cmd.find,
			"match_files": cmd.match_files,
			"write": cmd.write,
		}
		with open(pakefile) as f:
			source = f.read()
		code = compile(source, pakefile, "exec")
		try:
			exec(code, injected)
		except Exception as e:
			raise PakeError("Unhandled exception while loading Pakefile") from e

	def update(self, target, force=False):
		"""Build target and any dependencies (if they are not up to date) and return
		the target's result"""
		rule, match = self.resolve(target)
		return rule.update(match, force=force)

	def get_deps(self, *targets):
		"""Get dependencies of each target as a tree {target: get_deps(dep)}"""
		result = {}
		for target in targets:
			rule, match = self.resolve(target)
			result[target] = rule.get_deps(match)
		return result

	def resolve(self, target):
		"""Find and return the rule that matches target"""
		for rule in self.rules:
			match = rule.match(target)
			if match is not None:
				return rule, match
		raise AssertionError("No rules matched (not even fallback rule)")

	def register(self, rule):
		# insort_right() will preserve insertion order of equal-priority rules
		insort_right(self.rules, rule, key=lambda rule: rule.PRIORITY)

	def needs_update(self, target, inputs):
		"""Compare inputs to previously-recorded inputs for target,
		and return True if the target needs updating (ie. if inputs differ)"""
		if target not in self.state.data:
			return True
		return self.state.data[target]["inputs"] != inputs

	def save_result(self, target, inputs, result):
		"""Save the new result for the given target, along with the inputs that were used."""
		self.state.data[target] = {
			"inputs": inputs,
			"result": result,
		}
		self.state.save()

	def get_result(self, target):
		"""Get the most recent result for the given target, even if it is out of date.
		Returns None if not previously built.
		"""
		return self.state.data[target]["result"] if target in self.state.data else None
