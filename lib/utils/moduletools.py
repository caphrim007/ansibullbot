#!/usr/bin/env python

import ast
import copy
import os
import shutil
import yaml
#from lib.utils.systemtools import *
from lib.utils.systemtools import run_command


class ModuleIndexer(object):

    EMPTY_MODULE = {
        'authors': [],
        'name': None,
        'namespaced_module': None,
        'deprecated': False,
        'deprecated_filename': None,
        'dirpath': None,
        'filename': None,
        'filepath': None,
        'fulltopic': None,
        'maintainers': [],
        'maintainers_key': None,
        'metadata': {},
        'repo_filename': None,
        'repository': 'ansible',
        'subtopic': None,
        'topic': None,
        'imports': []
    }

    def __init__(self, maintainers=None):
        self.modules = {}
        self.maintainers = maintainers or {}
        #self.checkoutdir = '/tmp/ansible.modules.checkout'
        self.checkoutdir = '~/.ansibullbot/cache/ansible.modules.checkout'
        self.checkoutdir = os.path.expanduser(self.checkoutdir)
        self.importmap = {}

    def create_checkout(self):
        """checkout ansible"""

        print('# creating checkout for module indexer')

        # cleanup
        if os.path.isdir(self.checkoutdir):
            shutil.rmtree(self.checkoutdir)

        cmd = "git clone http://github.com/ansible/ansible --recursive %s" \
            % self.checkoutdir
        (rc, so, se) = run_command(cmd)
        print str(so) + str(se)

    def update_checkout(self):
        """rebase + pull + update the checkout"""

        print('# updating checkout for module indexer')
        #success = True

        cmd = "cd %s ; git pull --rebase" % self.checkoutdir
        (rc, so, se) = run_command(cmd)
        print str(so) + str(se)

        # If rebase failed, recreate the checkout
        if rc != 0:
            self.create_checkout()
            return

        cmd = "cd %s ; git submodule update --recursive" % self.checkoutdir
        (rc, so, se) = run_command(cmd)
        print str(so) + str(se)

        # if update fails, recreate the checkout
        if rc != 0:
            self.create_checkout()

    def _find_match(self, pattern, exact=False):

        match = None
        for k,v in self.modules.iteritems():
            if v['name'] == pattern:
                match = v
                break
        if not match:
            # search by key ... aka the filepath
            for k,v in self.modules.iteritems():
                if k == pattern:
                    match = v
                    break
        if not match and not exact:
            # search by properties
            for k,v in self.modules.iteritems():
                for subkey in v.keys():
                    if v[subkey] == pattern:
                        match = v
                        break
                if match:
                    break
        return match

    def find_match(self, pattern, exact=False):
        '''Exact module name matching'''
        if not pattern:
            return None

        # https://github.com/ansible/ansible/issues/19755
        if pattern == 'setup':
            pattern = 'system/setup.py'

        match = self._find_match(pattern, exact=exact)
        if not match and not exact:
            # check for just the basename
            #   2617: ansible-s-extras/network/cloudflare_dns.py
            bname = os.path.basename(pattern)
            match = self._find_match(bname)

            if not match:
                # check for deprecated name
                #   _fireball -> fireball
                match = self._find_match('_' + bname)

        return match

    def is_valid(self, mname):
        match = self.find_match(mname)
        if match:
            return True
        else:
            return False

    def get_repository_for_module(self, mname):
        match = self.find_match(mname)
        if match:
            return match['repository']
        else:
            return None

    def get_ansible_modules(self):
        """Make a list of known modules"""

        # manage the checkout
        if not os.path.isdir(self.checkoutdir):
            self.create_checkout()
        else:
            self.update_checkout()

        #(Epdb) pp module
        #u'wait_for'
        #(Epdb) pp self.module_indexer.is_valid(module)
        #False

        matches = []
        module_dir = os.path.join(self.checkoutdir, 'lib/ansible/modules')
        module_dir = os.path.expanduser(module_dir)
        for root, dirnames, filenames in os.walk(module_dir):
            for filename in filenames:
                if 'lib/ansible/modules' in root and \
                        not filename == '__init__.py' and \
                        (filename.endswith('.py') or filename.endswith('.ps1')):
                    matches.append(os.path.join(root, filename))

        matches = sorted(set(matches))

        # figure out the names
        for match in matches:
            mdict = copy.deepcopy(self.EMPTY_MODULE)

            mdict['filename'] = os.path.basename(match)

            dirpath = os.path.dirname(match)
            dirpath = dirpath.replace(self.checkoutdir + '/', '')
            mdict['dirpath'] = dirpath

            filepath = match.replace(self.checkoutdir + '/', '')
            mdict['filepath'] = filepath

            mdict.update(
                self.split_topics_from_path(filepath)
            )

            mdict['repo_filename'] = mdict['filepath']\
                .replace('lib/ansible/modules/%s/' % mdict['repository'], '')

            # clustering/consul
            mdict['namespaced_module'] = mdict['repo_filename']
            mdict['namespaced_module'] = \
                mdict['namespaced_module'].replace('.py', '')
            mdict['namespaced_module'] = \
                mdict['namespaced_module'].replace('.ps1', '')

            mname = os.path.basename(match)
            mname = mname.replace('.py', '')
            mname = mname.replace('.ps1', '')
            mdict['name'] = mname

            # deprecated modules
            if mname.startswith('_'):
                mdict['deprecated'] = True
                deprecated_filename = \
                    os.path.dirname(mdict['namespaced_module'])
                deprecated_filename = \
                    os.path.join(deprecated_filename, mname[1:] + '.py')
                mdict['deprecated_filename'] = deprecated_filename
            else:
                mdict['deprecated_filename'] = mdict['repo_filename']

            mkey = mdict['filepath']
            self.modules[mkey] = mdict

        # grep the authors:
        for k,v in self.modules.iteritems():
            mfile = os.path.join(self.checkoutdir, v['filepath'])
            authors = self.get_module_authors(mfile)
            self.modules[k]['authors'] = authors

        # meta is a special module
        self.modules['meta'] = copy.deepcopy(self.EMPTY_MODULE)
        self.modules['meta']['name'] = 'meta'
        self.modules['meta']['repo_filename'] = 'meta'

        # custom fixes
        newitems = []
        for k,v in self.modules.iteritems():

            # include* is almost always an ansible/ansible issue
            # https://github.com/ansible/ansibullbot/issues/214
            if k.endswith('/include.py'):
                self.modules[k]['repository'] = 'ansible'
            # https://github.com/ansible/ansibullbot/issues/214
            if k.endswith('/include_vars.py'):
                self.modules[k]['repository'] = 'ansible'
            if k.endswith('/include_role.py'):
                self.modules[k]['repository'] = 'ansible'

            # ansible maintains these
            if 'include' in k:
                self.modules[k]['maintainers'] = ['ansible']

            # deprecated modules are annoying
            if v['name'].startswith('_'):

                dkey = os.path.dirname(v['filepath'])
                dkey = os.path.join(dkey, v['filename'].replace('_', '', 1))
                if dkey not in self.modules:
                    nd = v.copy()
                    nd['name'] = nd['name'].replace('_', '', 1)
                    newitems.append((dkey, nd))

        for ni in newitems:
            self.modules[ni[0]] = ni[1]

        # parse metadata
        self.set_module_metadata()

        # parse imports
        self.set_module_imports()

        # depends on metadata now ...
        self.set_maintainers()

        return self.modules

    def set_maintainers(self):
        '''Define the maintainers for each module'''
        mkeys = self.maintainers.keys()
        for k,v in self.modules.iteritems():
            if not v['filepath']:
                continue
            best_match = None
            for mkey in mkeys:
                if mkey in v['filepath']:
                    if not best_match:
                        best_match = mkey
                        continue
                    if len(mkey) > len(best_match):
                        best_match = mkey
            if best_match:
                self.modules[k]['maintainers_key'] = best_match
                self.modules[k]['maintainers'] = self.maintainers[best_match]
            else:
                #import epdb; epdb.st()
                if v['metadata'].get('supported_by') in ['committer', 'core']:
                    self.modules[k]['maintainers_key'] = best_match
                    self.modules[k]['maintainers'] = ['ansible']

    def split_topics_from_path(self, module_file):
        subpath = module_file.replace('lib/ansible/modules/', '')
        path_parts = subpath.split('/')
        topic = path_parts[0]

        if len(path_parts) > 2:
            subtopic = path_parts[1]
            fulltopic = '/'.join(path_parts[0:2])
        else:
            subtopic = None
            fulltopic = path_parts[0]

        tdata = {
            'fulltopic': fulltopic,
            'namespace': fulltopic,
            'topic': topic,
            'subtopic': subtopic
        }

        #import epdb; epdb.st()
        return tdata

    def get_module_authors(self, module_file):
        """Grep the authors out of the module docstrings"""

        authors = []
        if not os.path.exists(module_file):
            return authors

        documentation = ''
        inphase = False

        with open(module_file, 'rb') as f:
            for line in f:
                if 'DOCUMENTATION' in line:
                    inphase = True
                    continue
                if line.strip().endswith("'''") or line.strip().endswith('"""'):
                    #phase = None
                    break
                if inphase:
                    documentation += line

        if not documentation:
            return authors

        # clean out any other yaml besides author to save time
        inphase = False
        author_lines = ''
        doc_lines = documentation.split('\n')
        for idx,x in enumerate(doc_lines):
            if x.startswith('author'):
                #print("START ON %s" % x)
                inphase = True
                #continue
            if inphase and not x.strip().startswith('-') and \
                    not x.strip().startswith('author'):
                #print("BREAK ON %s" % x)
                inphase = False
                break
            if inphase:
                author_lines += x + '\n'

        if not author_lines:
            return authors

        ydata = {}
        try:
            ydata = yaml.load(author_lines)
        except Exception as e:
            print e
            #import epdb; epdb.st()
            return authors

        # quit early if the yaml was not valid
        if not ydata:
            return authors

        # sometimes the field is 'author', sometimes it is 'authors'
        if 'authors' in ydata:
            ydata['author'] = ydata['authors']

        # quit if the key was not found
        if 'author' not in ydata:
            return authors

        if type(ydata['author']) != list:
            ydata['author'] = [ydata['author']]

        for author in ydata['author']:
            if '@' in author:
                words = author.split()
                for word in words:
                    if '@' in word and '(' in word and ')' in word:
                        if '(' in word:
                            word = word.split('(')[-1]
                        if ')' in word:
                            word = word.split(')')[0]
                        word = word.strip()
                        if word.startswith('@'):
                            word = word.replace('@', '', 1)
                            authors.append(word)

        return authors

    def fuzzy_match(self, repo=None, title=None, component=None):
        '''Fuzzy matching for modules'''

        # authorized_keys vs. authorized_key
        if component and component.endswith('s'):
            #import epdb; epdb.st()
            tm = self.find_match(component[:-1])
            if tm:
                return tm['name']
            #import epdb; epdb.st()

        match = None
        known_modules = []

        for k,v in self.modules.iteritems():
            known_modules.append(v['name'])

        title = title.lower()
        title = title.replace(':', '')
        title_matches = [x for x in known_modules if x + ' module' in title]

        if not title_matches:
            title_matches = [x for x in known_modules
                             if title.startswith(x + ' ')]
            if not title_matches:
                title_matches = \
                    [x for x in known_modules if ' ' + x + ' ' in title]

        # don't do singular word matching in title for ansible/ansible
        cmatches = None
        if component:
            cmatches = [x for x in known_modules if x in component]
            cmatches = [x for x in cmatches if not '_' + x in component]

            # use title ... ?
            if title_matches:
                cmatches = [x for x in cmatches if x in title_matches]

            if cmatches:
                if len(cmatches) >= 1:
                    match = cmatches[0]
                if not match:
                    if 'docs.ansible.com' in component:
                        pass
                    else:
                        pass
                print("module - component matches: %s" % cmatches)

        if not match:
            if len(title_matches) == 1:
                match = title_matches[0]
            else:
                print("module - title matches: %s" % title_matches)

        #import epdb; epdb.st()
        return match

    def is_multi(self, rawtext):
        '''Is the string a list or a glob of modules?'''
        if rawtext:
            lines = rawtext.split('\n')

            # clean up lines
            lines = [x.strip() for x in lines if x.strip()]
            lines = [x for x in lines if len(x) > 2]

            if len(lines) > 1:
                return True

            if lines:
                if lines[0].strip().endswith('*'):
                    return True

        return False

    # https://github.com/ansible/ansible-modules-core/issues/3831
    def multi_match(self, rawtext):
        '''Return a list of matches for a given glob or list of names'''
        matches = []
        lines = rawtext.split('\n')
        lines = [x.strip() for x in lines if x.strip()]
        for line in lines:
            # is it an exact name, a path, a globbed name, a globbed path?
            if line.endswith('*'):
                thiskey = line.replace('*', '')
                keymatches = []
                for k in self.modules.keys():
                    if thiskey in k:
                        keymatches.append(k)
                for k in keymatches:
                    matches.append(self.modules[k].copy())
            else:
                match = self.find_match(line)
                if match:
                    matches.append(match)

        # unique the list
        tmplist = []
        for x in matches:
            if x not in tmplist:
                tmplist.append(x)
        if matches != tmplist:
            matches = [x for x in tmplist]

        return matches

    def set_module_metadata(self):
        for k,v in self.modules.iteritems():
            if not v['filepath']:
                continue
            mfile = os.path.join(self.checkoutdir, v['filepath'])
            if not mfile.endswith('.py'):
                # metadata is only the .py files ...
                ext = mfile.split('.')[-1]
                mfile = mfile.replace('.' + ext, '.py', 1)

            self.modules[k]['metadata'].update(self.get_module_metadata(mfile))

    def get_module_metadata(self, module_file):
        meta = {}

        if not os.path.isfile(module_file):
            return meta

        rawmeta = ''
        inphase = False
        with open(module_file, 'rb') as f:
            for line in f:
                if line.startswith('ANSIBLE_METADATA'):
                    inphase = True
                    #continue
                if line.startswith('DOCUMENTATION'):
                    break
                if inphase:
                    rawmeta += line
        rawmeta = rawmeta.replace('ANSIBLE_METADATA =', '', 1)
        rawmeta = rawmeta.strip()
        try:
            meta = ast.literal_eval(rawmeta)
        except SyntaxError:
            pass

        return meta

    def set_module_imports(self):
        for k,v in self.modules.iteritems():
            if not v['filepath']:
                continue
            mfile = os.path.join(self.checkoutdir, v['filepath'])
            self.modules[k]['imports'] = self.get_module_imports(mfile)

    def get_module_imports(self, module_file):

        #import ansible.module_utils.nxos
        #from ansible.module_utils.netcfg import NetworkConfig, dumps
        #from ansible.module_utils.network import NetworkModule

        mimports = []

        with open(module_file, 'rb') as f:
            for line in f:
                line = line.strip()
                line = line.replace(',', '')
                if line.startswith('import') or \
                        ('import' in line and 'from' in line):
                    lparts = line.split()
                    if line.startswith('import '):
                        mimports.append(lparts[1])
                    elif line.startswith('from '):
                        mpath = lparts[1] + '.'
                        for spath in lparts[3:]:
                            mimports.append(mpath + spath)

        return mimports
