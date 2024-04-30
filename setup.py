from setuptools import setup, find_packages

setup(
	name='pake',
	version='0.0.1',
	author='ekimekim',
	author_email='pake@ekime.kim',
	description='A python-powered Make-like',
	packages=find_packages(),
	install_requires=[
		'argh',
	],
	entry_points = {
		'console_scripts': ['pake=pake.__main__:entrypoint'],
	},
)
