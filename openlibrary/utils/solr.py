"""Python library for accessing Solr"""

import logging
import re
from typing import List, Optional, TypeVar
from collections.abc import Callable, Iterable

import requests
import web

from urllib.parse import urlencode, urlsplit


logger = logging.getLogger("openlibrary.logger")


T = TypeVar('T')


class Solr:
    def __init__(self, base_url):
        self.base_url = base_url
        self.host = urlsplit(self.base_url)[1]
        self.session = requests.Session()

    def escape(self, query):
        r"""Escape special characters in the query string

        >>> solr = Solr("")
        >>> solr.escape("a[b]c")
        'a\\[b\\]c'
        """
        chars = r'+-!(){}[]^"~*?:\\'
        pattern = "([%s])" % re.escape(chars)
        return web.re_compile(pattern).sub(r'\\\1', query)

    def get(
        self,
        key: str,
        fields: list[str] = None,
        doc_wrapper: Callable[[dict], T] = web.storage,
    ) -> Optional[T]:
        """Get a specific item from solr"""
        logger.info(f"solr /get: {key}, {fields}")
        resp = self.session.get(
            f"{self.base_url}/get",
            params={'id': key, **({'fl': ','.join(fields)} if fields else {})},
        ).json()

        # Solr returns {doc: null} if the record isn't there
        return doc_wrapper(resp['doc']) if resp['doc'] else None

    def get_many(
        self,
        keys: Iterable[str],
        fields: Iterable[str] = None,
        doc_wrapper: Callable[[dict], T] = web.storage,
    ) -> list[T]:
        if not keys:
            return []
        logger.info(f"solr /get: {keys}, {fields}")
        resp = self.session.get(
            f"{self.base_url}/get",
            params={
                'ids': ','.join(keys),
                **({'fl': ','.join(fields)} if fields else {}),
            },
        ).json()
        return [doc_wrapper(doc) for doc in resp['response']['docs']]

    def select(
        self,
        query,
        fields=None,
        facets=None,
        rows=None,
        start=None,
        doc_wrapper=None,
        facet_wrapper=None,
        **kw,
    ):
        """Execute a solr query.

        query can be a string or a dictionary. If query is a dictionary, query
        is constructed by concatinating all the key-value pairs with AND condition.
        """
        params = {'wt': 'json'}

        for k, v in kw.items():
            # convert keys like facet_field to facet.field
            params[k.replace('_', '.')] = v

        params['q'] = self._prepare_select(query)

        if rows is not None:
            params['rows'] = rows
        params['start'] = start or 0

        if fields:
            params['fl'] = ",".join(fields)

        if facets:
            params['facet'] = "true"
            params['facet.field'] = []

            for f in facets:
                if isinstance(f, dict):
                    name = f.pop("name")
                    for k, v in f.items():
                        params[f"f.{name}.facet.{k}"] = v
                else:
                    name = f
                params['facet.field'].append(name)

        # switch to POST request when the payload is too big.
        # XXX: would it be a good idea to switch to POST always?
        payload = urlencode(params, doseq=True)
        url = self.base_url + "/select"
        if len(payload) < 500:
            url = url + "?" + payload
            logger.info("solr request: %s", url)
            json_data = self.session.get(url, timeout=10).json()
        else:
            logger.info("solr request: %s ...", url)
            headers = {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            }
            json_data = self.session.post(
                url, data=payload, headers=headers, timeout=10
            ).json()
        return self._parse_solr_result(
            json_data, doc_wrapper=doc_wrapper, facet_wrapper=facet_wrapper
        )

    def _parse_solr_result(self, result, doc_wrapper, facet_wrapper):
        response = result['response']

        doc_wrapper = doc_wrapper or web.storage
        facet_wrapper = facet_wrapper or (
            lambda name, value, count: web.storage(locals())
        )

        d = web.storage()
        d.num_found = response['numFound']
        d.docs = [doc_wrapper(doc) for doc in response['docs']]

        if 'facet_counts' in result:
            d.facets = {}
            for k, v in result['facet_counts']['facet_fields'].items():
                d.facets[k] = [
                    facet_wrapper(k, value, count) for value, count in web.group(v, 2)
                ]

        if 'highlighting' in result:
            d.highlighting = result['highlighting']

        if 'spellcheck' in result:
            d.spellcheck = result['spellcheck']

        return d

    def _prepare_select(self, query):
        def escape(v):
            # TODO: improve this
            return v.replace('"', r'\"').replace("(", "\\(").replace(")", "\\)")

        def escape_value(v):
            if isinstance(v, tuple):  # hack for supporting range
                return f"[{escape(v[0])} TO {escape(v[1])}]"
            elif isinstance(v, list):  # one of
                return "(%s)" % " OR ".join(escape_value(x) for x in v)
            else:
                return '"%s"' % escape(v)

        if isinstance(query, dict):
            op = query.pop("_op", "AND")
            if op.upper() != "OR":
                op = "AND"
            op = " " + op + " "

            q = op.join(f'{k}:{escape_value(v)}' for k, v in query.items())
        else:
            q = query
        return q


if __name__ == '__main__':
    import doctest

    doctest.testmod()
