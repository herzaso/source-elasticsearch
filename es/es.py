from copy import copy
import elasticsearch
import panoply

SCROLL_DURRATION = "1m"
BATCH_SIZE = 400
DESTINATION = "elasticsearch_{_index}_{_type}"
# https://www.elastic.co/guideen/elasticsearch/reference/current/mapping-uid-field.html
IDPATTERN = "{_type}#{_id}" # same as _uid

cat_api = elasticsearch.client.CatClient


class ElasticsearchSource(panoply.DataSource):

    indices = None
    index = None
    es = None
    scroll_id = None
    scroll_page = 0
    cur_total = 0
    loaded = 0
    num_hits = 0

    def __init__(self, source, options):
        super(ElasticsearchSource, self).__init__(source, options)

        if not source.get("destination"):
                source["destination"] = DESTINATION

        if not source.get("idpattern"):
                source["idpattern"] = IDPATTERN

        self.indices = copy(source.get("indices", []))
        self.inc_key = source.get("incKey")
        self.inc_val = source.get("incVal")
        self.excludes = source.get("excludes")

        host, port = source.get("host").split(":")
        self.es = elasticsearch.Elasticsearch([
            { "host": str(host), "port": str(port) }
        ])

    def get_indices(self):
        fields = ["index", "docs.count"]
        indices = cat_api(self.es).indices(
            format = "json",
            h = ",".join(fields)
        )

        return [ i["index"] for i in indices ]

    def read(self):
        self.index = self._get_index()
        if not self.index:
            return None

        self.log( "Processing index: %s" % self.index )

        if not self.scroll_id:
            data = self._search(self.index)
        else:
            self.scroll_page += 1
            self.log( "Scrolling page %d scroll_id: %s" % (
                self.scroll_page,
                self.scroll_id
            ))
            data = self.es.scroll(
                scroll_id = self.scroll_id,
                scroll = SCROLL_DURRATION
            )

        self.scroll_id = data["_scroll_id"]

        took = data.get("took", 0)
        hits = data.get("hits", {})
        docs = hits.get("hits", [])
        self.cur_total = hits.get("total", 0)

        self.num_hits = len(docs)
        self.loaded += self.num_hits

        progress_msg = "%s: Loaded %d of %s in %d ms" % (
            self.index, self.loaded,
            self.cur_total, took
        )
        self.progress(self.num_hits, self.cur_total, progress_msg)
        self.log(progress_msg)

        if self.loaded == self.cur_total:
            self.log("%s: Finished" % self.index)
            self.es.clear_scroll( scroll_id = self.scroll_id)
            self._reset_index()

        # If no documents are returned continue to the next batch
        return docs or self.read()

    def _search(self, index):
        search_opts = {
            "index": index,
            "body": self._build_query(),
            "scroll": SCROLL_DURRATION,
            "size": BATCH_SIZE,
            "_source_exclude": self.excludes
        }

        self.log( "Executing search:", search_opts )
        return self.es.search( **search_opts )

    def _build_query(self):
        params = {
            "sort": [ "_doc" ]
        }
        if self.inc_key and self.inc_val:
            inc_query = {}
            inc_query[self.inc_key] = { "gte": self.inc_val }
            params["query"] = {
                "bool": {
                    "filter": {
                        "range": inc_query
                    }
                }
            }
        return params

    def _get_index(self):
        index = None
        try:
            index = self.indices.pop() if not self.index else self.index
        except IndexError:
            pass

        return index

    def _reset_index(self):
        self.index = None
        self.scroll_id = None
        self.scroll_page = self.cur_total = self.loaded = self.num_hits = 0