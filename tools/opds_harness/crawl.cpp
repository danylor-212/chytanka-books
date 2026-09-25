// Crawl an OPDS catalogue the way the Chytanka firmware browses it.
//
// Uses the firmware's own OpdsParser (streamed in 512-byte chunks, capacity
// MAX_OPDS_FEED_ENTRIES) and UrlUtils::buildUrl, and reproduces
// OpdsBookBrowserActivity's navigation logic:
//   * fetchFeed(): prev-page link is inserted as the first NAV row, next-page
//     link appended as the last NAV row;
//   * navigateToEntry(): history.push(currentPath);
//                        currentPath = buildUrl(buildUrl(server, currentPath), href);
//   * navigateBack(): currentPath = history.pop() (empty history -> home);
//   * downloadBook(): url = buildUrl(buildUrl(server, currentPath), href).
// URLs under --base are served from --dir (the local public/ tree).
//
// Usage: opds_crawl --server URL --base URL --dir DIR [--expect-books N] [--walk] [--quiet]
// Exit != 0 on any parse error, empty feed, truncated feed, missing file,
// unreachable book count mismatch, or a failed back-navigation check.
#include <OpdsParser.h>
#include <UrlUtils.h>

#include <cstdio>
#include <cstring>
#include <deque>
#include <fstream>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace {
std::string server, base, dir;
bool quiet = false;
int errors = 0;

struct Row {
  OpdsEntryType type;
  std::string title, author, href;
};

bool localPath(const std::string& url, std::string& out) {
  if (url.rfind(base, 0) != 0) return false;
  out = dir + "/" + url.substr(base.size());
  return true;
}

bool fileExists(const std::string& p) {
  std::ifstream f(p, std::ios::binary);
  return f.good();
}

// Returns false on error. Mirrors fetchFeed(): rows incl. synthetic prev/next NAV rows.
bool fetchFeed(const std::string& path, std::vector<Row>& rows) {
  rows.clear();
  const std::string url = UrlUtils::buildUrl(server, path);
  std::string lp;
  if (!localPath(url, lp)) {
    std::printf("!! feed outside --base: %s\n", url.c_str());
    ++errors;
    return false;
  }
  std::ifstream in(lp, std::ios::binary);
  if (!in) {
    std::printf("!! 404 feed: %s (%s)\n", url.c_str(), lp.c_str());
    ++errors;
    return false;
  }
  std::stringstream ss;
  ss << in.rdbuf();
  const std::string xml = ss.str();
  auto entries = std::make_unique<OpdsEntry[]>(MAX_OPDS_FEED_ENTRIES);
  OpdsParser parser(entries.get(), MAX_OPDS_FEED_ENTRIES);
  const auto* p = reinterpret_cast<const uint8_t*>(xml.data());
  for (size_t off = 0; off < xml.size(); off += 512) parser.write(p + off, std::min<size_t>(512, xml.size() - off));
  parser.flush();
  if (parser.error()) {
    std::printf("!! parse error in %s\n", url.c_str());
    ++errors;
    return false;
  }
  if (parser.wasTruncated()) {
    std::printf("!! truncated (> %zu entries): %s\n", MAX_OPDS_FEED_ENTRIES, url.c_str());
    ++errors;
  }
  if (!parser.getPrevPageUrl().empty()) rows.push_back({OpdsEntryType::NAVIGATION, "< prev", "", parser.getPrevPageUrl()});
  for (const auto& e : parser.getEntries()) rows.push_back({e.type, e.title, e.author, e.href});
  if (!parser.getNextPageUrl().empty()) rows.push_back({OpdsEntryType::NAVIGATION, "next >", "", parser.getNextPageUrl()});
  if (parser.getEntryCount() == 0) {
    std::printf("!! empty feed: %s\n", url.c_str());
    ++errors;
    return false;
  }
  return true;
}

std::string resolve(const std::string& currentPath, const std::string& href) {
  const std::string feedUrl = UrlUtils::buildUrl(server, currentPath);
  return UrlUtils::buildUrl(feedUrl, href);
}
}  // namespace

int main(int argc, char** argv) {
  long expectBooks = -1;
  bool walk = false;
  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    if (a == "--server" && i + 1 < argc) server = argv[++i];
    else if (a == "--base" && i + 1 < argc) base = argv[++i];
    else if (a == "--dir" && i + 1 < argc) dir = argv[++i];
    else if (a == "--expect-books" && i + 1 < argc) expectBooks = std::stol(argv[++i]);
    else if (a == "--walk") walk = true;
    else if (a == "--quiet") quiet = true;
  }
  if (server.empty() || base.empty() || dir.empty()) {
    std::fprintf(stderr, "usage: opds_crawl --server URL --base URL --dir DIR [--expect-books N] [--walk]\n");
    return 2;
  }

  // ---- 1. breadth-first crawl of every navigation link
  // Paths are what the activity stores in currentPath; the root is "" (server URL).
  std::deque<std::pair<std::string, int>> queue{{"", 0}};
  std::set<std::string> seenFeeds{UrlUtils::buildUrl(server, "")};
  std::map<std::string, std::set<std::string>> booksByUrl;  // book url -> feeds listing it
  int maxDepth = 0, feeds = 0, navRows = 0, bookRows = 0;
  std::vector<Row> rows;
  while (!queue.empty()) {
    auto [path, depth] = queue.front();
    queue.pop_front();
    if (!fetchFeed(path, rows)) continue;
    ++feeds;
    maxDepth = std::max(maxDepth, depth);
    size_t nNav = 0, nBook = 0;
    for (const auto& r : rows) {
      const std::string target = resolve(path, r.href);
      if (r.type == OpdsEntryType::NAVIGATION) {
        ++nNav;
        if (seenFeeds.insert(target).second) queue.push_back({target, depth + 1});
      } else {
        ++nBook;
        std::string lp;
        if (!localPath(target, lp) || !fileExists(lp)) {
          std::printf("!! book 404: %s (listed in %s)\n", target.c_str(), UrlUtils::buildUrl(server, path).c_str());
          ++errors;
        }
        booksByUrl[target].insert(UrlUtils::buildUrl(server, path));
      }
    }
    navRows += nNav;
    bookRows += nBook;
    if (!quiet)
      std::printf("  depth %d | %3zu nav %3zu books | %s\n", depth, nNav, nBook, UrlUtils::buildUrl(server, path).c_str());
  }
  std::printf("crawl: %d feeds, max depth %d, %d nav rows, %d book rows, %zu unique books\n", feeds, maxDepth, navRows,
              bookRows, booksByUrl.size());
  if (expectBooks >= 0 && static_cast<long>(booksByUrl.size()) != expectBooks) {
    std::printf("!! expected %ld unique books, reachable %zu\n", expectBooks, booksByUrl.size());
    ++errors;
  }

  // ---- 2. scripted walk: always open the first real sub-feed until a book row appears,
  //         "download" it, then press Back until home, checking each step restores the path.
  if (walk) {
    std::vector<std::string> history;
    std::string currentPath;
    std::vector<std::string> trail{"(root)"};
    fetchFeed(currentPath, rows);
    for (int step = 0; step < 12; ++step) {
      const Row* book = nullptr;
      const Row* nav = nullptr;
      for (const auto& r : rows) {
        if (r.type == OpdsEntryType::BOOK && !book) book = &r;
        if (r.type == OpdsEntryType::NAVIGATION && !nav && r.title != "< prev" && r.title != "next >") nav = &r;
      }
      if (book) {
        const std::string url = resolve(currentPath, book->href);
        std::string lp;
        const bool ok = localPath(url, lp) && fileExists(lp);
        std::printf("walk: %s -> download «%s» %s\n", [&] {
          std::string s;
          for (auto& t : trail) s += (s.empty() ? "" : " > ") + t;
          return s;
        }().c_str(), book->title.c_str(), ok ? "OK" : "MISSING");
        if (!ok) ++errors;
        break;
      }
      if (!nav) {
        std::printf("!! walk: dead end at %s\n", currentPath.c_str());
        ++errors;
        break;
      }
      history.push_back(currentPath);
      currentPath = resolve(currentPath, nav->href);
      trail.push_back(nav->title);
      if (!fetchFeed(currentPath, rows)) break;
    }
    // Back to home
    const size_t levels = history.size();
    std::string expected;
    while (!history.empty()) {
      currentPath = history.back();
      history.pop_back();
      if (!fetchFeed(currentPath, rows)) break;
    }
    if (currentPath != "") {
      std::printf("!! walk: back navigation did not return to the root path\n");
      ++errors;
    } else {
      std::printf("walk: %zu x Back -> root feed again (%zu rows); one more Back -> home screen\n", levels, rows.size());
    }
  }
  std::printf("%s (%d error%s)\n", errors ? "FAIL" : "OK", errors, errors == 1 ? "" : "s");
  return errors ? 1 : 0;
}
