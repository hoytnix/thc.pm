from dns import resolver
import os
import shutil
import time
import hashlib
import ipaddress
import re

import click
import markdown
from jinja2 import Environment, PackageLoader, BaseLoader, FileSystemLoader
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from webptools import webplib as webp
from yaml import load, dump

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper

domain_pattern = re.compile('(([\da-zA-Z_])([_\w-]{,62})\.){,127}(([\da-zA-Z])[_\w-]{,61})?([\da-zA-Z]\.((xn\-\-[a-zA-Z\d]+)|([a-zA-Z\d]{2,})))$', re.IGNORECASE)



class Watcher:
    DIRECTORY_TO_WATCH = "./assets"

    def __init__(self):
        self.observer = Observer()

    def run(self):
        event_handler = Handler()
        self.observer.schedule(event_handler, self.DIRECTORY_TO_WATCH, recursive=True)
        self.observer.start()
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            self.observer.stop()
        self.observer.join()


class Handler(FileSystemEventHandler):

    @staticmethod
    def on_any_event(event):
        if event.is_directory:
            return None

        elif event.event_type == 'created':
            # Take any action here when a file is first created.
            print("Received created event - %s." % event.src_path)

        elif event.event_type == 'modified':
            # Taken any action here when a file is modified.
            print("Received modified event - %s." % event.src_path)

        builder()


def parse_spf(domain):
    ips = set()
    answers = resolver.resolve(domain.strip(), "TXT")
    for rdata in answers:
        splitted = rdata.to_text().split(' ')
        for split in splitted:
            split = split.strip()

            if split.startswith('ip4:') or split.startswith('ip6:'):
                split = split.lstrip('ip4:')
                split = split.lstrip('ip6:')

                try:
                    ipaddress.ip_address(split)
                    ips.add(split)
                    continue
                except ValueError:
                    pass

                try:
                    ipaddress.ip_network(split)
                    ips.add(split)
                    continue
                except ValueError:
                    pass

            if split.startswith('include:'):
                split = split.lstrip('include:')
                if domain_pattern.match(split):
                    ips.update(parse_spf(split))
                    continue

            #print("Unrecognized entry", split)

    return ips


def load_yamls_in_order(path):
    db = {}
    fps = sorted([fp for fp in os.listdir(path)])
    for fp in fps:
        with open(path + fp, 'r') as stream:
            o = load(stream, Loader=Loader)
            try:
                for k in o:
                    db[k] = o[k]
            except:
                pass
    return db

def build_template(template_key, config, page_name, output_path, root=False):
    env = Environment(loader=FileSystemLoader(searchpath="./assets"))
    template = env.get_template('templates/' + template_key)

    config['page_name'] = page_name
    try:
        config['body'] = Environment(loader=BaseLoader).from_string(markdown.markdown(open('./assets/pages/%s.jinja2' % page_name).read())).render(**config)
    except:
        pass

    html = template.render(**config)

    if output_path.split('/').__len__() > 3:
        n = 1
        search_paths = output_path.split('/')[2:]
        try:
            search_paths.remove('index.html')
        except:
            pass
        for path_key in search_paths:
            try:
                path = './dist/' + '/'.join(search_paths[:n])
                os.mkdir(path)
            except:
                pass
            n += 1

    with open(output_path, 'w+') as stream:
        stream.write(html)


def builder():
    # Import Configs
    app_config = load_yamls_in_order('./configs/')
    models = load_yamls_in_order('./models/')
    blueprints = load_yamls_in_order('./blueprints/')

    # Sitemap Data
    sitemap = []

    # Build static pages
    for template_key in blueprints:
        template_options = blueprints[template_key]

        # one-to-one
        if type(template_options) is dict:
            build_template(template_key + '.jinja2', {**app_config, **template_options}, template_key, './dist/%s/index.html' % template_key)
        # one-to-many
        else:
            for page in template_options:
                page_name = [x for x in page.keys()][0]
                page_options = page[page_name] or {}
                build_template(template_key + '.jinja2', {**app_config, **page_options}, page_name, './dist/%s/index.html' % page_name)
                sitemap.append(page_name)

    # Build modeled pages
    for model_key in models:
        model = models[model_key]
        templates = model['templates']
        items = model['items']
        for template in templates:
            t = templates[template]
            if type(t) is str: # list views
                build_template(template + '.jinja2', {**app_config, **{'items': items}}, t, './dist/%s/index.html' % t)
                sitemap.append(t)
            else: # detail views
                t_search = [k for k in t][0]
                t_glob = t[t_search]

                kvs = {}
                for page_name in items:
                    item = items[page_name]

                    if t_glob.endswith('*'):
                        page_options = {**app_config, **item}
                        build_template(template + '.jinja2', page_options, t_glob.replace('*', page_name), './dist/%s/index.html' % t_glob.replace('*', page_name))
                        sitemap.append(t_glob.replace('*', page_name))
                    else:
                        for v in item[t_search]:
                            if not v in kvs:
                                kvs[v] = []
                            kvs[v].append({**item, **{'key': page_name}})

                if t_glob.endswith('[*]'):
                    for k in kvs:
                        page_options = {**app_config, **{'kvs': kvs[k], 'title': k}}
                        build_template(template + '.jinja2', page_options, t_glob.replace('[*]', k), './dist/%s/index.html' % t_glob.replace('[*]', k))
                        sitemap.append(t_glob.replace('[*]', k))

    # Compile special items
    build_template('home.jinja2', {**app_config, **blueprints['pages']['home']}, 'home', './dist/index.html')
    build_template('404.jinja2', {**app_config}, None, './dist/404.html')
    build_template('sitemap.xml', {**app_config, **{'urls': sitemap}}, None, './dist/sitemap.xml')
    build_template('robots.txt', {**app_config}, None, './dist/robots.txt')

    # Minify Images
    img_task = False
    for (root, dirs, files) in os.walk('assets/static/img'):
        if files.__len__() > 0:
            img_task = True
        break

    if img_task:
        os.system("imagemin --plugin=pngquant assets/static/img/*.png --out-dir=assets/static/img/min")
        os.system("imagemin --plugin=mozjpeg assets/static/img/*.jpeg --out-dir=assets/static/img/min")
        os.system("imagemin --plugin=mozjpeg assets/static/img/*.jpg --out-dir=assets/static/img/min")
        os.system("imagemin --plugin=gifsicle assets/static/img/*.gif --out-dir=assets/static/img/min")
        os.system("imagemin --plugin=svgo assets/static/img/*.svg --out-dir=assets/static/img/min")

        for (root, dirs, files) in os.walk('assets/static/img'):
            for file in files:
                old_fp = root + '/' + file
                raw_fp = root + '/raw/' + file
                webp_fp = root + '/webp/' + '.'.join(file.split('.')[:-1]) + '.webp'

                shutil.move(old_fp, raw_fp)
                
                if not os.path.exists(webp_fp):
                    webp.cwebp(raw_fp, webp_fp, "-q 80")
            break

    # Collect static assets
    try:
        shutil.rmtree('dist/static')
    except:
        pass
    shutil.copytree(src='assets/static', dst='dist/static')
    
    # Print Firebase IP Range To Text
    domain = '_cloud-netblocks.googleusercontent.com'
    with open('firebase_ips.txt', 'r') as stream:
        new_dns = sorted(parse_spf(domain=domain))
        old_dns = sorted(stream.read().split('\n'))
        if new_dns != old_dns:
            print('Firebase IP Range Has Changed')
            with open('firebase_ips_2.txt', 'w+') as _stream:
                _stream.write('\n'.join(new_dns))


@click.group()
def cli():
    pass


@cli.command()
def build():
    builder()


@cli.command()
def watch():
    w = Watcher()
    w.run()


if __name__ == '__main__':
    cli()