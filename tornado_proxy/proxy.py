#!/usr/bin/env python
#
# Simple asynchronous HTTP proxy with tunnelling (CONNECT).
#
# GET/POST proxying based on
# http://groups.google.com/group/python-tornado/msg/7bea08e7a049cf26
#
# Copyright (C) 2012 Senko Rasic <senko.rasic@dobarkod.hr>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import logging
import os
import sys
import socket
#from urlparse import urlparse
from urllib.parse import urlparse
from io import BytesIO

import tornado.httpserver
import tornado.ioloop
import tornado.iostream
import tornado.web
import tornado.httpclient
import tornado.httputil
import tornado.gen

logger = logging.getLogger('tornado_proxy')
logger.setLevel(logging.DEBUG)

__all__ = ['ProxyHandler', 'run_proxy']


def get_proxy(url):
    url_parsed = urlparse(url, scheme='http')
    proxy_key = '%s_proxy' % url_parsed.scheme
    return os.environ.get(proxy_key)


def parse_proxy(proxy):
    proxy_parsed = urlparse(proxy, scheme='http')
    return proxy_parsed.hostname, proxy_parsed.port


def fetch_request(url, callback, **kwargs):
    proxy = get_proxy(url)
    if proxy:
        logger.debug('Forward request via upstream proxy %s', proxy)
        tornado.httpclient.AsyncHTTPClient.configure(
            'tornado.curl_httpclient.CurlAsyncHTTPClient')
        host, port = parse_proxy(proxy)
        kwargs['proxy_host'] = host
        kwargs['proxy_port'] = port

    req = tornado.httpclient.HTTPRequest(url, **kwargs)
    client = tornado.httpclient.AsyncHTTPClient()
    #future = client.fetch(req, callback, raise_error=False)
    #future = client.fetch(req, raise_error=False, callback=callback)
    future = client.fetch(req, raise_error=False)
    def future_callback(p_future):
        response = p_future.result()
        callback(response)
    future.add_done_callback(future_callback)
    #future.add


class ProxyHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ['GET', 'POST', 'CONNECT']
    
    def compute_etag(self):
        return None # disable tornado Etag

    #@tornado.web.asynchronous
    @tornado.gen.coroutine
    def get(self):
        logger.debug('Handle %s request to %s', self.request.method,
                     self.request.uri)

        def handle_response(response):
            if (response.error and not
                    isinstance(response.error, tornado.httpclient.HTTPError)):
                self.set_status(500)
                self.write('Internal server error:\n' + str(response.error))
            else:
                self.set_status(response.code, response.reason)
                self._headers = tornado.httputil.HTTPHeaders() # clear tornado default header
                
                for header, v in response.headers.get_all():
                    if header not in ('Content-Length', 'Transfer-Encoding', 'Content-Encoding', 'Connection'):
                        self.add_header(header, v) # some header appear multiple times, eg 'Set-Cookie'
                
                if response.body:                   
                    self.set_header('Content-Length', len(response.body))
                    self.write(response.body)
            self.finish()

        body = self.request.body
        if not body:
            body = None
        try:
            if 'Proxy-Connection' in self.request.headers:
                del self.request.headers['Proxy-Connection'] 
            fetch_request(
                self.request.uri, handle_response,
                method=self.request.method, body=body,
                headers=self.request.headers, follow_redirects=False,
                allow_nonstandard_methods=True)
        except tornado.httpclient.HTTPError as e:
            if hasattr(e, 'response') and e.response:
                handle_response(e.response)
            else:
                self.set_status(500)
                self.write('Internal server error:\n' + str(e))
                self.finish()

    #@tornado.web.asynchronous
    @tornado.gen.coroutine
    def post(self):
        return self.get()

    #@tornado.web.asynchronous
    @tornado.gen.coroutine
    def connect(self):
        logger.debug('Start CONNECT to %s', self.request.uri)
        host, port = self.request.uri.split(':')
        client = self.request.connection.stream

        def read_from_client():
            future = client.read_bytes(1024, partial=True)
            future.add_done_callback(read_from_client_callback)
            return future

        def read_from_client_callback(future):
            try:
                data = future.result()
                logger.debug(f'read_from_client_callback len: {len(data)}')
                upstream.write(data)
                return read_from_client()
            except:
                logger.debug('client closed')

        def read_from_upstream():
            future = upstream.read_bytes(1024, partial=True)
            future.add_done_callback(read_from_upstream_callback)
            return future

        def read_from_upstream_callback(future):
            try:
                data = future.result()
                client.write(data)
                return read_from_upstream()
            except:
                logger.debug('upstream closed')
        

        def client_close(data=None):
            logger.debug('client_close')
            if upstream.closed():
                return
            if data:
                upstream.write(data)
            upstream.close()

        def upstream_close(data=None):
            logger.debug('upstream_close')
            if client.closed():
                return
            if data:
                client.write(data)
            client.close()

        @tornado.gen.coroutine
        def start_tunnel():
            logger.debug('CONNECT tunnel established to %s', self.request.uri)
            #client.read_until_close(client_close, read_from_client)
            #read_buffer = yield client.read_until_close()
            client.set_close_callback(client_close)
            upstream.set_close_callback(upstream_close)
            yield client.write(b'HTTP/1.0 200 Connection established\r\n\r\n')
#            while not client.closed() and not upstream.closed():
            logger.debug('manipulating streams')
                #down_stream_buffer = yield client.read_bytes(1024000, partial=True)
                #yield upstream.write(down_stream_buffer)
                #logger.debug(f'down_stream_buffer size {len(down_stream_buffer)}')
            read_from_client()
 #               up_stream_buffer = yield upstream.read_bytes(1024000, partial=True)
  #              yield client.write(up_stream_buffer)
  #              logger.debug(f'up_stream_buffer size {len(up_stream_buffer)}')
            read_from_upstream()
            #while wrote:
            #    wrote = yield client.read_into(self.upstream)
                
            #self.read_from_client(read_buffer)
            #self.clien_close(read_buffer)
            #client_close()
            #upstream.read_until_close(upstream_close, read_from_upstream)
            #upstream_read_buffer = yield upstream.read_until_close()
            #self.upstream_close(upstream_read_buffer)
            #wrote = yield upstream.read_into(self.upstream)
            #while wrote:
            #    wrote = yield client.read_into(self.upstream)

        def on_proxy_response(data=None):
            if data:
                first_line = data.splitlines()[0]
                http_v, status, text = first_line.split(None, 2)
                if int(status) == 200:
                    logger.debug('Connected to upstream proxy %s', proxy)
                    start_tunnel()
                    return

            self.set_status(500)
            self.finish()

        def start_proxy_tunnel():
            logger.debug('start_proxy_tunnel')
            upstream.write('CONNECT %s HTTP/1.1\r\n' % self.request.uri)
            upstream.write('Host: %s\r\n' % self.request.uri)
            upstream.write('Proxy-Connection: Keep-Alive\r\n\r\n')
            upstream.read_until('\r\n\r\n', on_proxy_response)

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
        upstream = tornado.iostream.IOStream(s)

        proxy = get_proxy(self.request.uri)
        if proxy:
            proxy_host, proxy_port = parse_proxy(proxy)
            upstream.connect((proxy_host, proxy_port), start_proxy_tunnel)
            logger.debug(f'upstream.connect via proxy {proxy_host} {proxy_port}')
        else:
            #upstream.connect((host, int(port)), start_tunnel)
            #connect_future = upstream.connect((host, int(port)))
            #connect_future.add_done_callback(lambda x: start_tunnel())
            logger.debug(f'upstream.connect {host}, {port}')
            yield upstream.connect((host, int(port)))
            yield start_tunnel()


def run_proxy(port, start_ioloop=True):
    """
    Run proxy on the specified port. If start_ioloop is True (default),
    the tornado IOLoop will be started immediately.
    """
    import asyncio
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    app = tornado.web.Application([
        (r'.*', ProxyHandler),
    ], debug=True)
    app.listen(port)
    ioloop = tornado.ioloop.IOLoop.instance()
    if start_ioloop:
        ioloop.start()

if __name__ == '__main__':
    port = 8888
    if len(sys.argv) > 1:
        port = int(sys.argv[1])

    print ("Starting HTTP proxy on port %d" % port)
    run_proxy(port)
