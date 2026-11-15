-- 005_regions_and_pages: a deal watch can be limited to travel months (a region "in the
-- summer"), and a deal source can be a web page read as a list of posts, not only an
-- RSS or Atom feed. Only adds columns; existing rows keep every month and stay feeds.

ALTER TABLE deal_watches ADD COLUMN months TEXT NOT NULL DEFAULT '[]';  -- JSON, 1-12; [] = any
ALTER TABLE deal_feeds ADD COLUMN kind TEXT NOT NULL DEFAULT 'feed';    -- feed | page
