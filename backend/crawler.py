"""
FastPost Social v3 - Business Website Crawler
Scrapes business websites to gather content for AI post generation
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from typing import Dict, List, Optional
import re
import time
import random


class BusinessCrawler:
      def __init__(self, max_pages: int = 10, timeout: int = 10):
                self.max_pages = max_pages
                self.timeout = timeout
                self.headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                }

      def crawl(self, base_url: str) -> Dict:
                """
                        Crawl a business website and extract useful content.
                                Returns structured data ready for AI content generation.
                                        """
                if not base_url.startswith("http"):
                              base_url = "https://" + base_url

                visited = set()
                to_visit = [base_url]
                pages_data = []

          while to_visit and len(visited) < self.max_pages:
                        url = to_visit.pop(0)
                        if url in visited:
                                          continue

                        try:
                                          time.sleep(random.uniform(0.5, 1.5))  # Polite delay
                response = requests.get(url, headers=self.headers, timeout=self.timeout)
                if response.status_code != 200:
                                      continue

                soup = BeautifulSoup(response.text, "html.parser")
                page_data = self._extract_page_data(soup, url)
                pages_data.append(page_data)
                visited.add(url)

                # Discover internal links
                if len(visited) < self.max_pages:
                                      new_links = self._extract_links(soup, base_url, url)
                                      to_visit.extend([l for l in new_links if l not in visited])

except Exception as e:
                print(f"[Crawler] Error crawling {url}: {e}")
                visited.add(url)

        return self._consolidate(pages_data, base_url)

    def _extract_page_data(self, soup: BeautifulSoup, url: str) -> Dict:
              """Extract useful text content from a single page"""
              # Remove noise elements
              for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                            tag.decompose()

              # Title
              title = soup.title.string.strip() if soup.title else ""

        # Meta description
              meta_desc = ""
              meta = soup.find("meta", attrs={"name": "description"})
              if meta:
                            meta_desc = meta.get("content", "")

              # H1, H2 headings
              headings = [h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3"])[:10]]

        # Body text (first 2000 chars to keep it efficient)
              body_text = soup.get_text(separator=" ", strip=True)
              body_text = re.sub(r"\s+", " ", body_text)[:2000]

        # Images with alt text (useful for understanding products)
              images = []
              for img in soup.find_all("img", alt=True)[:5]:
                            alt = img.get("alt", "").strip()
                            if alt and len(alt) > 3:
                                              images.append(alt)

                        # Prices (useful for party rental businesses)
                        prices = re.findall(r"\$[\d,]+(?:\.\d{2})?", body_text)

        # Services/products keywords
        service_keywords = self._extract_service_keywords(body_text)

        return {
                      "url": url,
                      "title": title,
                      "meta_description": meta_desc,
                      "headings": headings,
                      "body_preview": body_text,
                      "images": images,
                      "prices": list(set(prices))[:10],
                      "service_keywords": service_keywords,
        }

    def _extract_service_keywords(self, text: str) -> List[str]:
        """Extract likely service/product names from text"""
        # Common party rental / event rental keywords
        rental_terms = [
                      "bounce house", "inflatable", "water slide", "tent", "table",
                      "chair", "linen", "canopy", "jumper", "combo unit", "obstacle",
                      "generator", "concession", "popcorn", "cotton candy", "snow cone",
                      "photo booth", "dance floor", "stage", "lighting", "sound system",
                  "delivery", "setup", "package", "rental"
        ]
        found = []
        text_lower = text.lower()
        for term in rental_terms:
                      if term in text_lower:
                                        found.append(term)
                                return found

    def _extract_links(self, soup: BeautifulSoup, base_url: str, current_url: str) -> List[str]:
              """Extract internal links from the page"""
        base_domain = urlparse(base_url).netloc
        links = []
        for a in soup.find_all("a", href=True):
                      href = a["href"]
            full_url = urljoin(current_url, href)
            parsed = urlparse(full_url)
            # Only follow internal links, skip anchors and files
            if (parsed.netloc == base_domain
                                    and not parsed.fragment
                                    and not re.search(r"\.(pdf|jpg|png|gif|zip|mp4)$", full_url, re.I)):
                                                      links.append(full_url.split("?")[0])  # Strip query params
        return list(set(links))

    def _consolidate(self, pages_data: List[Dict], base_url: str) -> Dict:
              """Merge all page data into a single structured summary"""
        all_headings = []
        all_services = []
        all_prices = []
        all_images = []
        text_samples = []
        business_name = ""

        for page in pages_data:
                      all_headings.extend(page["headings"])
            all_services.extend(page["service_keywords"])
            all_prices.extend(page["prices"])
            all_images.extend(page["images"])
            if page["body_preview"]:
                              text_samples.append(page["body_preview"][:500])
                          if not business_name and page["title"]:
                business_name = page["title"].split("|")[0].split("-")[0].strip()

        # Deduplicate
        all_services = list(set(all_services))
        all_prices = list(set(all_prices))

        return {
                      "base_url": base_url,
                      "business_name": business_name,
            "pages_count": len(pages_data),
                      "services": all_services,
                      "prices": all_prices[:10],
                      "key_headings": list(set(all_headings))[:15],
                      "image_descriptions": list(set(all_images))[:10],
                      "text_samples": text_samples[:3],
                      "summary": f"Business at {base_url} offering: {', '.join(all_services[:8])}" if all_services else f"Business at {base_url}"
        }
