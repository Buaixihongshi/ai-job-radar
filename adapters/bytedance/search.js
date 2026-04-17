/* @meta
{
  "name": "bytedance/search",
  "description": "搜索字节跳动社招岗位 (jobs.bytedance.com)",
  "domain": "jobs.bytedance.com",
  "args": {
    "keyword": {"required": true, "description": "搜索关键词 (如 AI测试, 大模型评测)"},
    "limit": {"required": false, "description": "每页数量 (默认 20)"},
    "offset": {"required": false, "description": "偏移量 (默认 0)"}
  },
  "readOnly": true,
  "example": "bb-browser site bytedance/search \"AI测试\""
}
*/

async function(args) {
  if (!args.keyword) return {error: 'Missing argument: keyword'};
  const limit = parseInt(args.limit) || 20;
  const offset = parseInt(args.offset) || 0;

  // Strategy 1: Try the internal API used by the SPA
  const apiPaths = [
    '/api/v1/search/position',
    '/api/v1/position/list',
    '/api/v2/search/position',
  ];

  for (const path of apiPaths) {
    try {
      const resp = await fetch(path, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          keyword: args.keyword,
          limit: limit,
          offset: offset,
          city_list: [],
          job_category_id_list: [],
          recruit_type: 'SOCIAL',
        }),
      });
      if (!resp.ok) continue;
      const data = await resp.json();
      if (data.code === 0 || data.code === 1000 || data.data) {
        const positions = data.data?.position_list || data.data?.list || data.data || [];
        if (Array.isArray(positions) && positions.length > 0) {
          const jobs = positions.map(p => ({
            jobId: String(p.id || p.position_id || p.job_id || ''),
            title: p.name || p.title || p.position_name || '',
            department: p.department || p.team || p.dept_name || '',
            city: p.city || p.city_name || (Array.isArray(p.city_list) ? p.city_list.join(',') : ''),
            description: p.description || p.content || p.job_desc || '',
            requirements: p.requirement || p.requirements || '',
            url: p.id ? `https://jobs.bytedance.com/experienced/position/${p.id}/detail` : '',
          }));
          return {keyword: args.keyword, offset, limit, total: data.data?.total || jobs.length, count: jobs.length, jobs};
        }
      }
    } catch(e) { /* try next */ }
  }

  // Strategy 2: Extract from page __NEXT_DATA__ or initial state
  try {
    const nextData = document.querySelector('#__NEXT_DATA__');
    if (nextData) {
      const parsed = JSON.parse(nextData.textContent);
      const pageProps = parsed?.props?.pageProps || {};
      const positions = pageProps.positionList || pageProps.data?.list || [];
      if (positions.length > 0) {
        const jobs = positions.map(p => ({
          jobId: String(p.id || ''),
          title: p.name || p.title || '',
          department: p.department || '',
          city: p.city || '',
          description: p.description || '',
          requirements: p.requirement || '',
          url: p.id ? `https://jobs.bytedance.com/experienced/position/${p.id}/detail` : '',
        }));
        return {keyword: args.keyword, source: '__NEXT_DATA__', count: jobs.length, jobs};
      }
    }
  } catch(e) { /* continue */ }

  // Strategy 3: Extract from window.__INITIAL_STATE__ or similar global state
  try {
    const state = window.__INITIAL_STATE__ || window.__NUXT__ || window.__APP_DATA__;
    if (state) {
      const jobs = [];
      const extract = (obj, depth) => {
        if (depth > 5 || !obj) return;
        if (Array.isArray(obj)) {
          for (const item of obj) {
            if (item && typeof item === 'object' && (item.name || item.title) && (item.id || item.position_id)) {
              jobs.push({
                jobId: String(item.id || item.position_id || ''),
                title: item.name || item.title || '',
                department: item.department || item.team || '',
                city: item.city || item.city_name || '',
                description: item.description || '',
                requirements: item.requirement || '',
                url: item.id ? `https://jobs.bytedance.com/experienced/position/${item.id}/detail` : '',
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
    hint: 'Open jobs.bytedance.com in your browser first, then run: bb-browser network requests --with-body --json to discover the actual API endpoint. Update this adapter accordingly.',
    keyword: args.keyword,
  };
}
