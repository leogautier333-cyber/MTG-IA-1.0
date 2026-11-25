import unittest
from unittest.mock import Mock

from services import spoiler_watcher

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Spike</title>
<content>text</content><link href="https://www.reddit.com/r/mtgfinance/comments/x"/></entry></feed>"""


class RedditFeedTests(unittest.TestCase):
    def test_feed_url_has_no_slash_before_rss_and_is_requested_as_is(self):
        self.assertEqual(spoiler_watcher.REDDIT_FEED, "https://www.reddit.com/r/mtgfinance.rss")
        session = Mock()
        session.get.return_value = Mock(content=ATOM)
        posts = spoiler_watcher.fetch_reddit_posts(session=session)
        self.assertEqual(session.get.call_args.args[0], "https://www.reddit.com/r/mtgfinance.rss")
        self.assertEqual(posts[0]["title"], "Spike")


if __name__ == "__main__":
    unittest.main()
