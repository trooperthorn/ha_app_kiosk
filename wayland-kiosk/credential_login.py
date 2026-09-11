"""Bounded credential entry in a verified HA document; no keyboard injection."""

import asyncio
import json
import logging
from urllib.parse import urlsplit
from security_config import effective_ha_url


def login_expression(options):
    parts = urlsplit(effective_ha_url(options))
    expected = f'{parts.scheme}://{parts.netloc}'
    # Check the origin in the same JS execution as reading/filling the form.
    # Values travel inside CDP, never through argv, logs, or a shell.
    args = json.dumps([expected, options['ha_username'], options['ha_password']])
    return r'''(async (expected, username, password) => {
      if (location.origin !== expected || location.pathname !== '/auth/authorize') return 'wrong-page';
      // Current HA uses a ha-auth-form component, not a native form.
      const flow = document.querySelector('ha-auth-flow');
      const haForm = flow && flow.querySelector('ha-auth-form');
      const submit = flow && flow.querySelector('.action ha-button');
      if (haForm && submit && !haForm.disabled && !submit.loading) {
        const names = new Set((haForm.schema || []).map(field => field.name));
        if (!names.has('username') || !names.has('password')) return 'waiting-form';
        const value = {...haForm.data, username, password};
        haForm.data = value;
        haForm.dispatchEvent(new CustomEvent('value-changed', {detail: {value}, bubbles: true, composed: true}));
        await haForm.updateComplete;
        await Promise.all([...haForm.querySelectorAll('*')].map(e => e.updateComplete));
        if (location.origin !== expected || location.pathname !== '/auth/authorize' || !haForm.isConnected) return 'wrong-page';
        submit.click();
        return 'submitted';
      }
      const all = [];
      function visit(root) {
        for (const e of root.querySelectorAll('*')) {
          all.push(e);
          if (e.shadowRoot) visit(e.shadowRoot);
        }
      }
      visit(document);
      const user = all.find(e => e.tagName === 'INPUT' &&
        (e.name === 'username' || e.autocomplete === 'username'));
      const pass = all.find(e => e.tagName === 'INPUT' && e.type === 'password');
      if (!user || !pass || !pass.form || user.form !== pass.form) return 'waiting-form';
      const form = pass.form;
      if (form.action && new URL(form.action, location.href).origin !== expected) return 'wrong-form';
      for (const [input, value] of [[user, username], [pass, password]]) {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
        input.dispatchEvent(new Event('input', {bubbles: true, composed: true}));
        input.dispatchEvent(new Event('change', {bubbles: true, composed: true}));
      }
      form.requestSubmit();
      return 'submitted';
    })(...''' + args + ')'


async def credential_login(options, command):
    if options.get('auth_method', 'none') != 'credentials':
        return
    expression = login_expression(options)
    attempts = max(1, min(120, int(float(options.get('login_delay', 10)))))
    for _ in range(attempts):
        await asyncio.sleep(1)
        result = await command('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True}, timeout=3, log_failure=False)
        value = result.get('result', {}).get('result', {}).get('value')
        if result.get('success') and value == 'submitted':
            logging.info('Credentials submitted to the verified Home Assistant login form.')
            return
    logging.warning('Verified login form unavailable; automatic login skipped. Use the kiosk login screen.')
