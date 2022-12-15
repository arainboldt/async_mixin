import os
import requests
import asyncio
import aiohttp
import socket
import json
import functools
from typing import Dict, List, Callable
from asyncio_throttle import Throttler
#enable nested async calls for use in Jupyter notebooks
try:
    import nest_asyncio
    nest_asyncio.apply(loop=asyncio.get_event_loop())
    #print('Nest Asyncio Activated')
except:
    pass


class AsyncHttpMixin:

    def call_limiter(func):
        @functools.wraps(func)
        def wrap(self, *args, **kwargs):
            if (self.remaining_calls is None):
                return func(self, *args, **kwargs)
            elif (self.remaining_calls > 0):
                return func(self, *args, **kwargs)
            else:
                raise ValueError(f'No remaining calls for API')
        return wrap

    @property
    def session(self):
        if not hasattr(self, '_session'):
            self._session = aiohttp.ClientSession(headers=self.headers, json_serialize=json.dumps)
        return self._session

    def reset(self):
        if hasattr(self, '_session'):
            del self._session

    def check_call_limit(self, resp):
        if hasattr(self, 'call_count_key') and hasattr(self, 'call_count_limit_key'):
            self.call_counts = resp.headers.get(self.call_count_key, 0)
            self.call_count_limit = resp.headers.get(self.call_count_limit_key, 0)
        
        if hasattr(self, 'call_limit_remaining_key'):
            self.remaining_calls = resp.headers.get(self.call_limit_remaining_key, 1)
        elif self.call_counts and self.call_count_limit:
            self.remaining_calls = self.call_count_limit - self.call_counts

    def set_rate_limit(self, n: int = 5, p: int = 60):
        """
        set client rate limit as number of calls per second
        Args:
            n: number of calls per period
            p: period length in seconds

        """
        self.throttler = Throttler(rate_limit=n, period=p)

    def _async_conn(self, loop: asyncio.AbstractEventLoop):
        """
        set client TCP connection with async event loop
        Args:
            loop: asynchronous event loop

        """
        self.conn = aiohttp.TCPConnector(loop=loop,
                                         force_close=True,
                                         family=socket.AF_INET,
                                         verify_ssl=False,
                                         )

    @call_limiter
    def get(self, url, headers):
        try:
            resp = requests.get(url, headers=headers)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as err:
            raise SystemExit(err)
        self.check_call_limit(resp)
        return resp.json()

    @call_limiter
    def post(self, url: str, data: dict=None):
        try:
            resp = requests.post(url, data=json.dumps(data), headers=self.headers)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as err:
            print(resp.json())
            raise SystemExit(err)
        self.check_call_limit(resp)
        return resp.json()

    @call_limiter
    async def async_get(self, url: str):
        """
        throttled asynchronous get call
        Args:
            url: target endpoint url

        Returns: call response as json/dict

        """
        async with self.throttler:
            async with self.session.get(url) as response:
                try:
                    assert response.status == 200
                except:
                    return {'data': {}, 'message': {f"Error: {response.reason}"}, 'meta': {} }
                self.check_call_limit(response)
                data = await response.json()
                return data

    @call_limiter
    async def async_post(self, url: str, payload: Dict):
        """
        throttled asynchronous post call
        Args:
            url: target endpoint url
            payload: json/dict of payload to be posted

        Returns: call response

        """
        async with self.throttler:
            async with self.session.post(url, json=payload) as response:
                try:
                    assert response.status == 200
                except Exception as e:
                    print(f'Post Error: {response.status}: {e}')
                    return {'data': {}, 'message': {f"Error: {response.reason}"}, 'meta': {}}
                self.check_call_limit(response)
                data = await response.json()
                return data

    async def process_gets(self, urls: List[str]):
        """
        process multiple get calls asynchronously
        Args:
            urls: list of target endpoint urls

        Returns: list of call responses

        """
        tasks = []
        async with self.session:
            for i in range(len(urls)):
                tasks.append(self.async_get(urls[i]))
            responses = await asyncio.gather(*tasks)
            return responses

    async def process_posts(self, urls: str, payloads: List[Dict]):
        """
        process multiple post calls asynchronously
        Args:
            urls: list of target endpoint urls
            payloads: list of dicts of payloads

        Returns: list of call responses

        """
        if isinstance(urls, str) or (len(urls) == 1):
            urls = [urls] * len(payloads)
        tasks = []
        async with self.session:
            for url, payload in zip(urls,payloads):
                tasks.append(self.async_post(url, payload))
            responses = await asyncio.gather(*tasks)
            return responses

    def pipeline(self, method: Callable, args: List=None, kwargs: Dict=None, unpack: bool=False):
        """
        non async method to process aynsc method with passed arguments
            intended as non-async handle for async methods
        Args:
            method: async method to be called
            args: list args to be passed to method
            kwargs: kwarg dict to be passed to method
            unpack: bool, whether to unpack args list

        Returns: call response(s)

        """
        if args is None:
            args = []
        if kwargs is None:
            kwargs = {}
        #get loop
        loop = asyncio.new_event_loop()
        #init connection
        self._async_conn(loop)
        res = None
        try:
            if len(args):
                if unpack:
                    res = loop.run_until_complete(method(*args))
                else:
                    res = loop.run_until_complete(method(args))
            elif len(kwargs):
                res = loop.run_until_complete(method(**kwargs))
            else:
                res = loop.run_until_complete(method())
        except Exception as e:
            #todo: perhaps better to log this instead
            import traceback
            import sys
            exc_info = sys.exc_info()
            traceback.print_exception(*exc_info)
        #be tidy, close loop
        loop.close()
        self.reset()
        return res