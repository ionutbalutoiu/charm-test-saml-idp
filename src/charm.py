#!/usr/bin/env python3
#
# Copyright 2021 Ionut Balutoiu
# See LICENSE file for licensing details.
#

import logging
import grp
import pwd
import subprocess
import os

from socket import gethostname as get_unit_hostname
from urllib.request import urlopen

from charmhelpers.contrib import ssl
from charmhelpers.core.hookenv import unit_get, resource_get
from charmhelpers.core.host import service_restart
from charmhelpers.fetch import apt_update, apt_install
from charmhelpers.fetch.archiveurl import ArchiveUrlFetchHandler

from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus
from ops.main import main

from utils import render_configs, retry_on_error

logger = logging.getLogger(__name__)

try:
    from lxml import etree  # NOQA:F401
except ImportError:
    logger.warning(
        'Failed to import the lxml module. Re-installing the charm venv')
    retry_on_error()(apt_update)(fatal=True)
    retry_on_error()(apt_install)(packages=['python3-pip'], fatal=True)
    pip_cmd = ['pip3', 'install', '--upgrade', '--force-reinstall',
               '--target=venv', '--requirement=requirements.txt']
    retry_on_error()(subprocess.check_call)(pip_cmd)
    from lxml import etree  # NOQA:F401


class TestSamlIdpCharm(CharmBase):
    BASE_DOWNLOAD_URL = ('https://github.com/simplesamlphp/simplesamlphp/'
                         'releases/download')
    DEST_DIR = '/var/simplesamlphp'
    APT_PACKAGES = ['php', 'php-xml', 'php-date', 'php-mbstring',
                    'apache2', 'libapache2-mod-php']
    APACHE_USER = 'www-data'
    APACHE_GROUP = 'www-data'
    IDP_METADATA_PATH = '/simplesaml/saml2/idp/metadata.php'
    UNIT_ACTIVE_STATUS = ActiveStatus('Unit is ready')

    def __init__(self, *args):
        super().__init__(*args)
        self._sp_metadata = None
        self.framework.observe(
            self.on.install,
            self.on_install)
        self.framework.observe(
            self.on.config_changed,
            self.on_config_changed)
        self.framework.observe(
            self.on.get_idp_metadata_action,
            self.on_get_idp_metadata_action)

    def on_install(self, _):
        retry_on_error()(apt_update)(fatal=True)
        retry_on_error()(apt_install)(packages=self.APT_PACKAGES, fatal=True)
        self.setup_simplesamlphp()
        self.setup_apache2()

    def on_config_changed(self, _):
        ctxt_gens = [
            {
                'template': 'simplesamlphp/config.php.j2',
                'output': '{0}/config/config.php'.format(self.DEST_DIR),
                'context': {}
            },
            {
                'template': 'simplesamlphp/authsources.php.j2',
                'output': '{0}/config/authsources.php'.format(self.DEST_DIR),
                'context': {
                    'user_name': self.config['auth-user-name'],
                    'user_password': self.config['auth-user-password']
                }
            },
            {
                'template': 'simplesamlphp/saml20-idp-hosted.php.j2',
                'output': '{0}/metadata/saml20-idp-hosted.php'.format(
                    self.DEST_DIR),
                'context': {}
            },
        ]
        render_configs(ctxt_gens)
        self.setup_saml_idp()

    def on_get_idp_metadata_action(self, event):
        metadata_url = 'http://{0}:{1}{2}'.format(
            unit_get('private-address'),
            self.config.get('http-port'),
            self.IDP_METADATA_PATH)
        event.set_results({'output': urlopen(metadata_url).read().decode()})

    def setup_simplesamlphp(self):
        if os.path.exists(self.DEST_DIR):
            os.rmdir(self.DEST_DIR)

        version = self.config.get('simple-saml-php-version')
        archive_handler = ArchiveUrlFetchHandler()
        retry_on_error()(archive_handler.install)(
            source='{0}/v{1}/simplesamlphp-{1}.tar.gz'.format(
                self.BASE_DOWNLOAD_URL, version),
            dest=os.path.dirname(self.DEST_DIR))
        os.rename('{0}-{1}'.format(self.DEST_DIR, version), self.DEST_DIR)

        key_file = '{0}/cert/server.pem'.format(self.DEST_DIR)
        cert_file = '{0}/cert/server.crt'.format(self.DEST_DIR)
        ssl.generate_selfsigned(keyfile=key_file, certfile=cert_file,
                                keysize=2048, cn=get_unit_hostname())
        uid = pwd.getpwnam(self.APACHE_USER).pw_uid
        gid = grp.getgrnam(self.APACHE_GROUP).gr_gid
        os.chown(key_file, uid, gid)
        os.chown(cert_file, uid, gid)

    def setup_apache2(self):
        os.makedirs('/etc/apache2/ssl', exist_ok=True)
        ssl.generate_selfsigned(
            keyfile='/etc/apache2/ssl/private.key',
            certfile='/etc/apache2/ssl/cert.crt',
            keysize=2048,
            cn=get_unit_hostname())

        ctxt_gens = [
            {
                'template': 'apache2/ports.conf.j2',
                'output': '/etc/apache2/ports.conf',
                'context': {
                    'http_port': self.config['http-port'],
                    'https_port': self.config['https-port']
                }
            },
            {
                'template': 'apache2/simplesamlphp.conf.j2',
                'output': '/etc/apache2/sites-available/simplesamlphp.conf',
                'context': {
                    'http_port': self.config['http-port'],
                    'https_port': self.config['https-port']
                }
            }
        ]
        render_configs(ctxt_gens)

        subprocess.check_call(
            ['a2enmod', 'ssl'])
        subprocess.check_call(
            ['a2dissite', '000-default.conf', 'default-ssl.conf'])
        subprocess.check_call(
            ['a2ensite', 'simplesamlphp.conf'])

        service_restart('apache2')

    def setup_saml_idp(self):
        if not self.sp_metadata:
            logger.warning('The SP metadata is not set yet')
            return

        render_configs([{
            'template': 'simplesamlphp/saml20-sp-remote.php.j2',
            'output': '{0}/metadata/saml20-sp-remote.php'.format(
                self.DEST_DIR),
            'context': {
                'sp_entity_id': self.sp_entity_id,
                'sp_assertion_cs': self.sp_assertion_consumer_service,
                'sp_single_logout_service': self.sp_single_logout_service,
            }
        }])
        service_restart('apache2')

        self.unit.status = self.UNIT_ACTIVE_STATUS

    @property
    def sp_metadata(self):
        if self._sp_metadata:
            return self._sp_metadata

        sp_metadata_path = resource_get('sp-metadata')
        if not os.path.exists(sp_metadata_path):
            return None

        with open(sp_metadata_path) as f:
            content = f.read()
            try:
                self._sp_metadata = etree.fromstring(content.encode())
            except etree.XMLSyntaxError:
                self.unit.status = BlockedStatus(
                    'sp-metadata resource is not a well-formed xml file')

        return self._sp_metadata

    @property
    def sp_entity_id(self):
        return self.sp_metadata.get('entityID')

    @property
    def sp_assertion_consumer_service(self):
        ns_map = self.sp_metadata.nsmap
        assertion_consumer_services = self.sp_metadata.find(
            'SPSSODescriptor', ns_map).findall(
                'AssertionConsumerService', ns_map)
        for acs in assertion_consumer_services:
            if acs.get('Binding').endswith('bindings:HTTP-POST'):
                return acs.get('Location')
        raise Exception(
            "Cannot find the bindings:HTTP-POST attribute in "
            "AssertionConsumerService elements from the SP metadata xml.")

    @property
    def sp_single_logout_service(self):
        ns_map = self.sp_metadata.nsmap
        single_logout_services = self.sp_metadata.find(
            'SPSSODescriptor', ns_map).findall(
                'SingleLogoutService', ns_map)
        for sls in single_logout_services:
            if sls.get('Binding').endswith('bindings:HTTP-Redirect'):
                return sls.get('Location')
        raise Exception(
            "Cannot find the bindings:HTTP-Redirect attribute in "
            "SingleLogoutService elements from the SP metadata xml.")


if __name__ == "__main__":
    main(TestSamlIdpCharm)
