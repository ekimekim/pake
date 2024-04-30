import argh

from .main import main

# Need a seperate callable for when run via setuptools
def entrypoint():
	argh.dispatch_command(main)

if __name__ == '__main__':
	entrypoint()
