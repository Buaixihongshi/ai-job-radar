/* @meta
{
  "name": "didi/search",
  "description": "搜索滴滴社招岗位 (talent.didiglobal.com)",
  "domain": "talent.didiglobal.com",
  "args": {
    "keyword": {"required": true, "description": "搜索关键词 (如 AI测试, 大模型评测)"},
    "page": {"required": false, "description": "页码 (默认 1)"},
    "size": {"required": false, "description": "每页数量 (默认 20)"}
  },
  "readOnly": true,
  "example": "bb-browser site didi/search \"AI测试\""
}
*/

async function(args) {
  if (!args.keyword) return {error: 'Missing argument: keyword'};
  const page = parseInt(args.page) || 1;
  const size = parseInt(args.size) || 20;

  // Strategy 1: Try MokaHR-style API (DiDi uses MokaHR for recruitment)
  const apiPaths = [
    '/api/position/list',
    '/api/v1/position/list',
    '/social/api/position/list',
    '/api/job/list',
    '/recruit/social/position/list',
  ];

  for (const path of apiPaths) {
    try {
      // Try POST
      let resp = await fetch(path, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          keyword: args.keyword,
          page: page,
          pageSize: size,
          recruitType: 'SOCIAL',
        }),
      });
      if (!resp.ok) {
        // Try GET
        const params = new URLSearchParams({keyword: args.keyword, page: String(page), pageSize: String(size)});
        resp = await fetch(path + '?' + params.toString(), {credentials: 'include'});
      }
      if (!resp.ok) continue;
      const data = await resp.json();
      const list = data.data?.list || data.data?.records || data.data?.items || data.data?.positions || data.list || data.data || [];
      if (Array.isArray(list) && list.length > 0) {
        const jobs = list.map(p => ({
          jobId: String(p.id || p.positionId || p.jobId || ''),
          title: p.name || p.positionName || p.jobName || p.title || '',
          department: p.department || p.deptName || p.teamName || '',
          city: Array.isArray(p.city) ? p.city.join(',') : (p.city || p.location || p.workLocation || ''),
          experience: p.workYear || p.experience || '',
          education: p.education || p.degree || '',
          description: p.description || p.jobDesc || '',
          requirements: p.requirement || '',
          url: (p.id || p.positionId) ? `https://talent.didiglobal.com/social/p/${p.id || p.positionId}` : '',
        }));
        return {keyword: args.keyword, page, size, total: data.data?.total || data.total || jobs.length, count: jobs.length, jobs};
      }
    } catch(e) { /* try next */ }
  }

  // Strategy 2: Extract from page state (SSR data / window state)
  try {
    const nextData = document.querySelector('#__NEXT_DATA__');
    if (nextData) {
      const parsed = JSON.parse(nextData.textContent);
      const pageProps = parsed?.props?.pageProps || {};
      const list = pageProps.positionList || pageProps.jobs || pageProps.data?.list || [];
      if (list.length > 0) {
        const jobs = list.map(p => ({
          jobId: String(p.id || ''),
          title: p.name || p.title || '',
          department: p.department || '',
          city: p.city || p.location || '',
          description: p.description || '',
          url: p.id ? `https://talent.didiglobal.com/social/p/${p.id}` : '',
        }));
        return {keyword: args.keyword, source: '__NEXT_DATA__', count: jobs.length, jobs};
      }
    }
  } catch(e) { /* continue */ }

  // Strategy 3: window global state
  try {
    const state = window.__INITIAL_STATE__ || window.__NUXT__ || window.__APP_DATA__ || window.__INITIAL_DATA__;
    if (state) {
      const jobs = [];
      const extract = (obj, depth) => {
        if (depth > 5 || !obj) return;
        if (Array.isArray(obj)) {
          for (const item of obj) {
            if (item && typeof item === 'object' && (item.name || item.positionName || item.jobName) && (item.id || item.positionId || item.jobId)) {
              jobs.push({
                jobId: String(item.id || item.positionId || item.jobId || ''),
                title: item.name || item.positionName || item.jobName || '',
                department: item.department || item.deptName || '',
                city: item.city || item.location || '',
                experience: item.workYear || item.experience || '',
                education: item.education || item.degree || '',
                description: item.description || item.jobDesc || '',
                requirements: item.requirement || '',
                url: item.id ? `https://talent.didiglobal.com/social/p/${item.id}` : '',
              });
            }
            extract(item, depth + 1);
          }
        } else if (typeof obj === 'object') {
          for (const v of Object.values(obj)) extract(v, depth + 1);
        }
      };
      extract(state, 0);
      if (jobs.length > 0) return {keyword: args.keyword, source: 'initialState', count: jobs.length, jobs};
    }
  } catch(e) { /* continue */ }

  return {
    error: 'Could not find job data',
    hint: 'Open talent.didiglobal.com/social in your browser first, then run: bb-browser network requests --with-body --json to discover the actual API endpoint. Update this adapter accordingly.',
    keyword: args.keyword,
  };
}
