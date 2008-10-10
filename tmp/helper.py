#!/usr/bin/python
# damn ugly script to help in the creation of webmin source package
#
# used to create module and theme archives out of the full webmin
# tarball, and seed control file content
# it skips core modules
#
# usage:
#   get tarball (eg. webmin-1.430.tar.gz)
#   tar -zxf webmin-1.430.tar.gz
#   helper.py webmin-1.430
#
#   will create modules/*.wbm, control and themes/*.wbt, control

import os
import sys
from os.path import *

class Plugin:
    def __init__(self, path, type):
        self.name = basename(path)
        self.path = path
        self.type = type
        self._parseinfo()

    def _parseinfo(self):
        self.info = {}
        for line in file(join(self.path, '%s.info' % self.type)).readlines():
            for attr in ('version', 'desc', 'longdesc', 'depends'):
                if line.startswith(attr+"="):
                    self.info[attr] = line.split("=")[1].strip()

    def create_archive(self):
        print "archive: %s" % self.name
        outdir = join(os.getcwd(), self.type + 's')
        if not exists(outdir):
            os.mkdir(outdir)
        
        outfile = join(outdir, self.name + ".wb" + self.type[0] + ".gz")
        os.system("tar -zcf %s -C %s %s" % (outfile,
                                            dirname(self.path),
                                            self.name))

    def create_control(self):
        print "control: %s" % self.name
        file('%ss/control' % self.type, 'a').write(self.get_control())

    def get_control(self):
        c = "Package: webmin-%s\n" % self.name
        c += "Architecture: any\n"
        c += "Depends: webmin (>= %s)" % (self.info['version'])
        try:
            for dep in self.info['depends'].split():
                if not dep.startswith('1.'): #versions
                    c += ", webmin-%s" % dep
        except:
            pass
        c += "\n"

        try:
            c += "Description: Webmin %s - %s\n" % (self.type,
                                                    self.info['desc'])
        except KeyError:
            c += "Description: Webmin %s - %s\n" % (self.type,
                                                    self.name.capitalize())

        try:
            #debian policy states long description should be max 60 chars
            longdesc = self.info['longdesc']
            while len(longdesc) > 60:
                char = 50
                while longdesc[char].isalpha():
                    char += 1
                c += " %s\n" % longdesc[0:char]
                longdesc = longdesc[char:].strip()

            if len(longdesc) <= 60:
                c += " %s\n" % longdesc

        except KeyError:
            pass

        return c + "\n"

def get_plugins(path):
    plugins = []
    for f in os.listdir(path):
        f = join(path, f)
        if exists(join(f, 'module.info')):
            plugins.append(Plugin(f, 'module'))

        if exists(join(f, 'theme.info')):
            plugins.append(Plugin(f, 'theme'))

    return plugins

def main():
    if len(sys.argv) != 2:
        print "usage: %s <path>" % sys.argv[0]
        sys.exit(1)

    for plugin in get_plugins(sys.argv[1]):
        if plugin.name in ('acl', 'cron', 'init', 'inittab', 'man', 'proc',
                           'servers', 'webmin', 'webminlog'):
            continue

        plugin.create_archive()
        plugin.create_control()


if __name__ == "__main__":
    main()

