# Open Source Core Licensing Strategy

## Overview

This document outlines the licensing strategy for the future **GrowAssistant Core** open source repository (Phase 2 of the hybrid strategy). The recommended license is **AGPL-3.0** (GNU Affero General Public License v3.0).

## Why AGPL-3.0?

### Perfect for Hybrid Business Models

**AGPL-3.0 is specifically designed for companies that:**
- Offer open source core + commercial platform
- Want to prevent competitors from making proprietary services
- Need to protect their business model while building community

### Industry Examples Using AGPL-3.0

**Successful companies using AGPL-3.0:**
- **Grafana** - Open source monitoring + Grafana Cloud
- **GitLab** - Community Edition (AGPL) + Enterprise Edition  
- **MongoDB** - Used AGPL before SSPL
- **Neo4j** - Community Edition (AGPL) + Enterprise
- **Supabase** - Open source + hosted platform

## AGPL-3.0 Key Features

### 1. **Network Copyleft Protection**

**The "Affero" clause:**
```
If you modify AGPL code and run it as a network service, 
you MUST make your modifications available as open source.
```

**This means:**
- ✅ Community can use and contribute freely
- ❌ Competitors can't take your core and make proprietary SaaS
- ✅ You can offer commercial licenses for proprietary use
- ❌ Cloud providers can't just host your code without contributing back

### 2. **Business Model Protection**

**How AGPL-3.0 protects your revenue:**

```
Scenario 1: Competitor wants to build competing SaaS
├─ Uses your AGPL core → Must open source their entire platform
├─ Violates AGPL terms → Legal action possible
└─ Buys commercial license → Revenue for you

Scenario 2: Enterprise wants proprietary deployment  
├─ AGPL requires open sourcing → Not acceptable for enterprise
└─ Buys commercial license → Revenue for you

Scenario 3: Community/Research use
├─ Uses AGPL core → Contributes improvements back
└─ Benefits everyone → Grows your ecosystem
```

### 3. **Dual Licensing Opportunity**

**You retain copyright, so you can offer:**
- **AGPL-3.0**: Free for open source use
- **Commercial License**: Paid for proprietary use
- **Enterprise License**: Custom terms for large deployments

## License Template for GrowAssistant Core

### AGPL-3.0 LICENSE File

```
GNU AFFERO GENERAL PUBLIC LICENSE
Version 3, 19 November 2007

Copyright (C) 2024 [Your Name/Company]

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

Additional permissions under GNU AGPL version 3 section 7:

If you modify this Program, or any covered work, by linking or combining it 
with proprietary libraries (or a modified version of those libraries), 
containing parts covered by the terms of [library licenses], the licensors 
of this Program grant you additional permission to convey the resulting work.

For commercial licensing options, please contact: [your-email@example.com]
```

### README Header for Core Repository

```markdown
# GrowAssistant Core

🌱 **Open Source IoT Growing Automation Engine**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![GitHub release](https://img.shields.io/github/release/sabbalot/grow-assistant-core.svg)](https://github.com/sabbalot/grow-assistant-core/releases)
[![Docker Pulls](https://img.shields.io/docker/pulls/sabbalot/grow-assistant-core.svg)](https://hub.docker.com/r/sabbalot/grow-assistant-core)

## About

GrowAssistant Core is the open source foundation for IoT-based growing automation. 
It provides basic sensor monitoring, rule-based automation, and a plugin architecture 
for community extensions.

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0).

**What this means:**
- ✅ **Free to use** for personal, research, and open source projects
- ✅ **Modify and distribute** as long as you share modifications
- ✅ **Commercial use** allowed if you comply with AGPL terms
- ❌ **Proprietary SaaS** requires commercial license

**Need a commercial license?** Contact us at [licensing@growassistant.com]

## Commercial Platform

For enterprise features, professional support, and hosted solutions, 
see [GrowAssistant Platform](https://github.com/sabbalot/grow-assistant-deployment).
```

## Implementation Strategy

### Phase 2: Open Source Core Launch

**1. Code Extraction (Month 6-12):**
```bash
# Create new repository
git clone --bare grow-assistant-services grow-assistant-core
cd grow-assistant-core

# Extract only core components
git filter-branch --subdirectory-filter core/ -- --all
```

**2. Repository Setup:**
```
grow-assistant-core/
├── LICENSE (AGPL-3.0)
├── README.md (with license badges)
├── CONTRIBUTING.md (CLA requirements)
├── src/
│   ├── sensors/ (basic sensor interfaces)
│   ├── automation/ (simple rule engine)
│   ├── data/ (data storage interfaces)
│   └── plugins/ (plugin architecture)
├── examples/
├── docs/
└── docker/
```

**3. Contributor License Agreement (CLA):**
```
All contributors must sign a CLA granting [Your Company] the right to:
- Relicense contributions under commercial licenses
- Use contributions in proprietary products
- Maintain copyright ownership for dual licensing
```

### Business Model Integration

**Clear differentiation between Core vs. Commercial:**

**Open Source Core (AGPL-3.0):**
- Basic sensor data collection
- Simple rule-based automation
- Plugin architecture
- Manual configuration
- Community support only
- Single-user deployment

**Commercial Platform (Proprietary):**
- AI-driven automation
- Professional UI/UX
- Cloud synchronization
- Multi-user/enterprise features
- Professional support & SLA
- Advanced analytics
- Automated setup

### Legal Protections

**1. Contributor License Agreement:**
```markdown
By contributing to GrowAssistant Core, you grant [Your Company]:
- Non-exclusive, perpetual, worldwide license to your contributions
- Right to sublicense contributions under different terms
- Right to use contributions in commercial products
```

**2. Trademark Protection:**
```markdown
"GrowAssistant" is a trademark of [Your Company].
- Core project can use "GrowAssistant Core"
- Derivatives cannot use "GrowAssistant" without permission
- Commercial platform retains exclusive trademark rights
```

**3. AGPL Compliance Monitoring:**
```markdown
We actively monitor for AGPL compliance:
- Automated license scanning
- Community reporting mechanisms
- Legal enforcement for violations
```

## Competitive Advantages

### Why AGPL-3.0 Works for You

**1. Prevents "AWS Problem":**
- Cloud providers can't just host your core without contributing
- Forces them to either contribute back or pay for commercial license

**2. Drives Commercial Conversion:**
- Enterprises uncomfortable with AGPL → commercial license
- SaaS companies need proprietary deployment → commercial license

**3. Community Building:**
- Research institutions love AGPL (no licensing fees)
- Open source contributors improve your product
- Educational use builds long-term relationships

**4. Legal Clarity:**
- Well-established license with clear terms
- Strong legal enforcement history
- Compatible with most open source ecosystems

## Alternative Licenses Considered

### MIT/Apache 2.0
❌ **Too permissive** - competitors could make proprietary SaaS  
❌ **No business model protection**  
✅ **Maximum adoption**

### GPL-3.0
❌ **No network copyleft** - SaaS loophole  
❌ **Less business-friendly**  
✅ **Strong copyleft protection**

### SSPL (Server Side Public License)
❌ **Not OSI-approved** - adoption issues  
❌ **Controversial** - ecosystem resistance  
✅ **Strong SaaS protection**

### Business Source License (BSL)
❌ **Not truly open source** - delayed open source  
❌ **Complex terms** - adoption barrier  
✅ **Clear commercial protection**

## **Winner: AGPL-3.0**
✅ **OSI-approved** open source license  
✅ **Strong business model protection**  
✅ **Industry proven** (Grafana, GitLab, etc.)  
✅ **Network copyleft** prevents SaaS exploitation  
✅ **Dual licensing** opportunities  

## Implementation Checklist

### Legal Setup
- [ ] Review AGPL-3.0 terms with legal counsel
- [ ] Prepare Contributor License Agreement
- [ ] Register trademarks if needed
- [ ] Set up commercial licensing terms

### Repository Preparation  
- [ ] Extract core components from private repos
- [ ] Add AGPL-3.0 LICENSE file
- [ ] Create comprehensive README with licensing info
- [ ] Set up CLA enforcement (GitHub integration)
- [ ] Add license headers to all source files

### Business Integration
- [ ] Create clear differentiation documentation
- [ ] Set up commercial licensing contact process
- [ ] Prepare compliance monitoring procedures
- [ ] Train team on AGPL vs commercial boundaries

---

**AGPL-3.0 gives you the perfect balance: vibrant open source community + protected commercial opportunities.** 