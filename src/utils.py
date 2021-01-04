"""
Utilities needed for the charm implementation
"""

import functools
import logging
import os
import time
import traceback

import jinja2

logger = logging.getLogger(__name__)


def _get_exception_details():
    return traceback.format_exc()


def retry_on_error(max_attempts=30, sleep_seconds=5, terminal_exceptions=[]):
    def _retry_on_error(func):
        @functools.wraps(func)
        def _exec_retry(*args, **kwargs):
            i = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except KeyboardInterrupt as ex:
                    logger.warning("Got a KeyboardInterrupt, skip retrying")
                    logger.error(ex)
                    raise
                except Exception as ex:
                    if any([isinstance(ex, tex)
                            for tex in terminal_exceptions]):
                        raise
                    i += 1
                    if i < max_attempts:
                        logger.warning(
                            "Exception occurred, retrying (%d/%d):\n%s",
                            i, max_attempts, _get_exception_details())
                        time.sleep(sleep_seconds)
                    else:
                        raise
        return _exec_retry
    return _retry_on_error


def render_template(template_file, output_file, context={}, searchpath="/"):
    template_loader = jinja2.FileSystemLoader(searchpath=searchpath)
    template_env = jinja2.Environment(loader=template_loader)
    template = template_env.get_template(template_file)

    with open(output_file, 'w') as f:
        f.write(template.render(context))


def render_configs(ctxt_generators):
    for ctxt_gen in ctxt_generators:
        render_template(
            template_file='{0}/src/templates/{1}'.format(
                os.environ.get('CHARM_DIR'),
                ctxt_gen['template']),
            output_file=ctxt_gen['output'],
            context=ctxt_gen['context'])
