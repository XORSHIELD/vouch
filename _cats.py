import urllib.request, re
UA={"User-Agent":"Mozilla/5.0"}
url="https://greenbook.nafdac.gov.ng/productCategories"
html=urllib.request.urlopen(urllib.request.Request(url,headers=UA), timeout=40).read().decode("utf-8","replace")
print(len(html))
txt=re.sub(r"<script.*?</script>","",html,flags=re.S)
txt=re.sub(r"<style.*?</style>","",txt,flags=re.S)
txt=re.sub(r"<[^>]+>"," ",txt)
txt=re.sub(r"\s+"," ",txt)
i=txt.lower().find("class")
print(txt[max(0,i-300):i+1200])
