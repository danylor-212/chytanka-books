// Host harness: run the Chytanka firmware's own OpdsParser against feed files.
//
// Feeds the XML in small chunks through the streaming Print interface, exactly
// like OpdsBookBrowserActivity does with the HTTP body, using the same entry
// capacity (MAX_OPDS_FEED_ENTRIES).
//
// Usage: opds_harness [--chunk N] [--feed-url URL] feed.xml [feed2.xml ...]
// With --feed-url, every href is resolved through the firmware's
// UrlUtils::buildUrl(feedUrl, href) and the resulting URL is printed.
// Exit code != 0 if any feed fails to parse, has no entries, was truncated,
// or contains a book href that is not absolute http(s).
#include <OpdsParser.h>
#include <UrlUtils.h>

#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>

static const char* typeName(OpdsEntryType t) { return t == OpdsEntryType::BOOK ? "BOOK" : "NAV "; }

int main(int argc, char** argv) {
  size_t chunk = 512;
  std::string feedUrl;
  int rc = 0;
  int files = 0;
  for (int i = 1; i < argc; ++i) {
    if (std::strcmp(argv[i], "--chunk") == 0 && i + 1 < argc) {
      chunk = std::stoul(argv[++i]);
      continue;
    }
    if (std::strcmp(argv[i], "--feed-url") == 0 && i + 1 < argc) {
      feedUrl = argv[++i];
      continue;
    }
    ++files;
    std::ifstream in(argv[i], std::ios::binary);
    if (!in) {
      std::fprintf(stderr, "cannot open %s\n", argv[i]);
      return 2;
    }
    std::stringstream ss;
    ss << in.rdbuf();
    const std::string xml = ss.str();

    auto entries = std::make_unique<OpdsEntry[]>(MAX_OPDS_FEED_ENTRIES);
    OpdsParser parser(entries.get(), MAX_OPDS_FEED_ENTRIES);
    const auto* p = reinterpret_cast<const uint8_t*>(xml.data());
    for (size_t off = 0; off < xml.size(); off += chunk) {
      parser.write(p + off, std::min(chunk, xml.size() - off));
    }
    parser.flush();

    std::printf("== %s (%zu bytes, chunk %zu)\n", argv[i], xml.size(), chunk);
    if (parser.error()) {
      std::printf("   PARSE ERROR (reason %d)\n", static_cast<int>(parser.getErrorReason()));
      rc = 1;
      continue;
    }
    size_t books = 0;
    for (const auto& e : parser.getEntries()) {
      std::printf("   %s | %-28s | %-24s | %s\n", typeName(e.type), e.title.c_str(), e.author.c_str(), e.href.c_str());
      if (!feedUrl.empty()) {
        std::printf("        -> device GET %s\n", UrlUtils::buildUrl(feedUrl, e.href).c_str());
      }
      if (e.type == OpdsEntryType::BOOK) {
        ++books;
        if (e.href.rfind("https://", 0) != 0 && e.href.rfind("http://", 0) != 0) {
          std::printf("   !! book href is not absolute: %s\n", e.href.c_str());
          rc = 1;
        }
      }
    }
    std::printf("   entries=%zu books=%zu truncated=%s next=%s prev=%s search=%s\n", parser.getEntryCount(), books,
                parser.wasTruncated() ? "YES" : "no", parser.getNextPageUrl().empty() ? "-" : parser.getNextPageUrl().c_str(),
                parser.getPrevPageUrl().empty() ? "-" : parser.getPrevPageUrl().c_str(),
                parser.getSearchTemplate().empty() ? "-" : parser.getSearchTemplate().c_str());
    if (parser.getEntryCount() == 0 || parser.wasTruncated()) rc = 1;
  }
  if (files == 0) {
    std::fprintf(stderr, "usage: %s [--chunk N] feed.xml...\n", argv[0]);
    return 2;
  }
  return rc;
}
