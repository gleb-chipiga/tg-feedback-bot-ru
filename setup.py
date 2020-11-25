import pathlib
import re

from setuptools import setup  # type: ignore

root = pathlib.Path(__file__).parent
txt = (root / 'feedback_bot' / '__init__.py').read_text('utf-8')
version = re.findall(r"^__version__ = '([^']+)'\r?$", txt, re.M)[0]

setup(
    name='tg-feedback-bot-ru',
    version=version,
    description='Telegram feedback bot',
    url='https://github.com/gleb-chipiga/tg-feedback-bot-ru',
    license='MIT',
    author='Gleb Chipiga',
    # author_email='',
    classifiers=[
        'Intended Audience :: Customer Service',
        'Intended Audience :: End Users/Desktop',
        'Intended Audience :: System Administrators',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'License :: OSI Approved :: MIT License',
        'Development Status :: 3 - Alpha',
        'Operating System :: POSIX',
        'Operating System :: MacOS :: MacOS X',
        'Operating System :: Microsoft :: Windows',
        'Topic :: Internet',
        'Topic :: Communications :: Chat',
        'Framework :: AsyncIO',
    ],
    packages=['feedback_bot'],
    python_requires='>=3.8,<3.10',
    install_requires=['aiotgbot[sqlite]>=0.6.0,<0.7.0', 'aiojobs',
                      'aiojobs-stubs>=0.2.2.post1', 'attrs', 'uvloop',
                      'more_itertools'],
    entry_points={
        'console_scripts': [
            'feedback-bot=feedback_bot.feedback_bot:main'
        ],
    }
)
