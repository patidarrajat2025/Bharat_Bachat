import i18n from "i18next";
import { initReactI18next } from "react-i18next";

/**
 * Central UI dictionary. The English key is intentionally the source string so
 * existing screens can be migrated without changing their business logic.
 */
const hi: Record<string, string> = {
  "Bharat Bachat":"भारत बचत","Aapki Bachat, Aapka Vikas":"आपकी बचत, आपका विकास",
  "Change temporary password":"अस्थायी पासवर्ड बदलें",
  "This is mandatory before any protected screen or API can be used.":"सुरक्षित स्क्रीन या सुविधा इस्तेमाल करने से पहले यह पासवर्ड बदलना अनिवार्य है।",
  "Current PIN / Password":"वर्तमान पिन / पासवर्ड","New PIN / Password":"नया पिन / पासवर्ड","Confirm New PIN / Password":"नए पिन / पासवर्ड की पुष्टि करें",
  "Saving…":"सहेजा जा रहा है…","Set New Password":"नया पासवर्ड सेट करें","Signing in…":"लॉगिन हो रहा है…",
  "Active group context":"सक्रिय समूह","Select group":"समूह चुनें","Open":"खोलें",
  "Share view":"शेयर दृश्य","Personal financial view":"व्यक्तिगत वित्तीय दृश्य","Choose one share or combine all active shares.":"एक शेयर चुनें या सभी सक्रिय शेयर देखें।",
  "All Active Shares":"सभी सक्रिय शेयर","Personal Money Growth":"व्यक्तिगत धन वृद्धि","Share-wise collection across active members.":"सक्रिय सदस्यों की शेयर-वार वसूली।",
  "Collected":"जमा","Pending":"बाकी","Partial":"आंशिक","Loan & Profit Snapshot":"ऋण और लाभ सारांश",
  "Active loans":"सक्रिय ऋण","Interest collected":"वसूल ब्याज","Loan repayments":"ऋण वापसी","Expenses":"खर्च",
  "Add Member":"सदस्य जोड़ें","Member":"सदस्य","Phone":"फोन","Shares":"शेयर","Status":"स्थिति","Profile":"प्रोफ़ाइल",
  "Actions":"कार्य","None":"कोई नहीं","Edit":"संपादित करें","Reset PIN":"पिन रीसेट","Photo":"फोटो","Remove Photo":"फोटो हटाएँ",
  "No members yet.":"अभी कोई सदस्य नहीं है।","Reset Credential":"क्रेडेंशियल रीसेट करें",
  "Date":"तारीख","Type":"प्रकार","Account":"खाता","Inflow":"आवक","Outflow":"जावक","Note":"नोट",
  "Member ID":"सदस्य आईडी","Name":"नाम","Total Savings":"कुल बचत","Active Loan":"सक्रिय ऋण","Pending Interest":"बाकी ब्याज","Kist This Month":"इस महीने की किस्त",
  "Choose financial view":"वित्तीय दृश्य चुनें","All active shares or a specific share":"सभी सक्रिय शेयर या कोई एक शेयर","All shares":"सभी शेयर",
  "Custom":"कस्टम","Share PDF":"शेयर PDF","Credit/Debit":"जमा/निकासी","Running Balance":"चलता बैलेंस","Receipt":"रसीद","PDF":"PDF",
  "No transactions for this range.":"इस अवधि में कोई लेन-देन नहीं है।",
  "Post Repayment":"वापसी दर्ज करें","Advance Loan Requests":"अग्रिम ऋण अनुरोध","Approve & Create Loan":"स्वीकृत करें और ऋण बनाएँ","Reject":"अस्वीकार करें",
  "Cash":"कैश","Bank":"बैंक","Save Repayment":"वापसी सहेजें","Principal":"मूलधन","Outstanding Principal":"बाकी मूलधन",
  "Expected Interest":"अपेक्षित ब्याज","Interest Due":"बाकी ब्याज","No loan history.":"ऋण इतिहास उपलब्ध नहीं है।",
  "Apply for Advance Loan":"अग्रिम ऋण के लिए आवेदन","Request History":"अनुरोध इतिहास","Submit Request":"अनुरोध भेजें",
  "Monthly Kist Collection":"मासिक किस्त वसूली","Select a member once, then post payment against any active shares.":"सदस्य चुनें और उसके सक्रिय शेयरों की किस्त दर्ज करें।",
  "Pay All Remaining":"सभी बाकी भरें","Select member":"सदस्य चुनें","Expected":"अपेक्षित","Paid":"जमा","Share":"शेयर","Payment":"भुगतान",
  "This entry":"यह प्रविष्टि","Save Share-wise Kist":"शेयर-वार किस्त सहेजें","Collection Rules":"वसूली नियम",
  "Partial payments supported":"आंशिक भुगतान उपलब्ध","Admin can collect a smaller amount now and the remaining balance later.":"एडमिन अभी कम राशि ले सकता है और बाकी बाद में ले सकता है।",
  "Duplicate protection":"डुप्लीकेट सुरक्षा","Payment date":"भुगतान तारीख","Interest / Penalty Inflow":"ब्याज / जुर्माना आवक",
  "Interest":"ब्याज","Penalty":"जुर्माना","Group":"समूह","Save Inflow":"आवक सहेजें",
  "Add Expense":"खर्च जोड़ें","Select":"चुनें","Other":"अन्य","Bill / Receipt proof":"बिल / रसीद प्रमाण","Save Expense":"खर्च सहेजें",
  "Add":"जोड़ें","Expense History":"खर्च इतिहास","Category":"श्रेणी","Amount":"राशि","Proof":"प्रमाण","View":"देखें",
  "Time":"समय","Actor":"कर्ता","Action":"कार्य","Entity":"रिकॉर्ड","Reference":"संदर्भ",
  "Create Loan":"ऋण बनाएँ","New":"नया","Pending Requests":"लंबित अनुरोध","Approve":"स्वीकृत करें","Create & Disburse Loan":"ऋण बनाएँ और जारी करें",
  "Groups":"समूह","Admins":"एडमिन","BC Groups / Tenants":"BC समूह / टेनेंट","Create Group":"समूह बनाएँ","Kist Setting":"किस्त सेटिंग",
  "Logo":"लोगो","Group Admins":"समूह एडमिन","Create Admin":"एडमिन बनाएँ","Admin":"एडमिन","Security":"सुरक्षा",
  "Reset Password":"पासवर्ड रीसेट","Save Kist Setting":"किस्त सेटिंग सहेजें","Reset Admin Credential":"एडमिन क्रेडेंशियल रीसेट करें",
  "Dashboard":"डैशबोर्ड","Members":"सदस्य","Financial Register":"वित्तीय रजिस्टर","Member Ledger":"सदस्य लेजर","Analytics":"विश्लेषण",
  "Personal Passbook":"व्यक्तिगत पासबुक","Group Loans":"समूह ऋण","Personal Loan":"व्यक्तिगत ऋण","Admin & Audit":"एडमिन और ऑडिट","Logout":"लॉगआउट",
  "Total Vault Balance":"कुल वॉल्ट बैलेंस","Cash-in-Hand":"हाथ में कैश","Bank Balance":"बैंक बैलेंस","Save":"सहेजें",
  "Monthly Contribution":"मासिक योगदान","Expense":"खर्च","Language":"भाषा","No data yet":"अभी डेटा नहीं है",
  "Good Morning":"सुप्रभात","Quick Actions":"त्वरित कार्य","Recent Activity":"हाल की गतिविधियाँ",
  "My Dashboard":"मेरा डैशबोर्ड","Your personal savings, wealth and loan snapshot":"आपकी व्यक्तिगत बचत, संपत्ति और ऋण का सारांश",
  "Operational financial overview":"वित्तीय संचालन का सारांश","Super Admin":"सुपर एडमिन",
  "Choose a BC group to open its operational dashboard":"ऑपरेशनल डैशबोर्ड खोलने के लिए BC समूह चुनें",
  "Select a group above. Global management is available from Admin & Audit.":"ऊपर समूह चुनें। वैश्विक प्रबंधन एडमिन और ऑडिट में उपलब्ध है।",
  "Group Dashboard":"समूह डैशबोर्ड",
  "Member onboarding, shares, profile images and credential control":"सदस्य, शेयर, प्रोफ़ाइल फोटो और क्रेडेंशियल प्रबंधन",
  "Digital cash book: opening balance → inflows → outflows → closing balance":"डिजिटल कैश बुक: शुरुआती बैलेंस → आवक → जावक → अंतिम बैलेंस",
  "Opening + Current":"शुरुआती + वर्तमान","Closing Balance":"अंतिम बैलेंस",
  "Savings, active loan balance and pending interest":"बचत, सक्रिय ऋण और बाकी ब्याज",
  "My Wealth Growth":"मेरी धन वृद्धि","Personal growth with share-wise analysis":"शेयर-वार व्यक्तिगत वित्तीय विश्लेषण",
  "Analytics & Wealth Growth":"विश्लेषण और धन वृद्धि","Real transaction-derived group pool and contribution trends":"वास्तविक लेन-देन और योगदान के आधार पर समूह रुझान",
  "Contributions":"योगदान","Interest + Penalty":"ब्याज + जुर्माना","Repayments":"ऋण वापसी",
  "Passbook":"पासबुक","Bank-style ledger with share filter and A4 PDF sharing":"शेयर फ़िल्टर के साथ बैंक-स्टाइल लेजर और A4 PDF",
  "Date Range":"तारीख अवधि","From":"से","To":"तक","Credit":"जमा","Debit":"निकासी",
  "Group Loans Overview":"समूह ऋण सारांश","Active loans, repayment progress and advance-loan requests":"सक्रिय ऋण, वापसी प्रगति और अग्रिम ऋण अनुरोध",
  "Personal Loan Dashboard":"व्यक्तिगत ऋण डैशबोर्ड","Your principal, interest breakdown, repayment history and advance request":"मूलधन, ब्याज, वापसी इतिहास और अग्रिम अनुरोध",
  "Closed Loan":"बंद ऋण",
  "Admin Control & Audit":"एडमिन नियंत्रण और ऑडिट","Post share-wise Kist, manage loans, expenses and immutable audit logs":"शेयर-वार किस्त, ऋण, खर्च और ऑडिट लॉग प्रबंधन",
  "Payments":"भुगतान","Loans":"ऋण","Audit":"ऑडिट","Month":"महीना",
  "One share = one financial unit":"एक शेयर = एक वित्तीय इकाई",
};
const en: Record<string,string> = Object.fromEntries(Object.keys(hi).map(k=>[k,k]));

const resources = { en:{translation:en}, hi:{translation:hi} };

i18n.use(initReactI18next).init({
  resources,
  lng: localStorage.getItem("bb-lang") || "en",
  fallbackLng: "en",
  interpolation: { escapeValue:false },
});
export const tr = (key:string) => i18n.t(key, { defaultValue:key });
export const trError = (message:string) => {
  const map:Record<string,string> = {
    "Invalid phone or password":"फोन या पासवर्ड गलत है",
    "Account inactive":"खाता निष्क्रिय है",
    "Authentication required":"लॉगिन आवश्यक है",
    "Invalid or expired token":"सेशन अमान्य या समाप्त हो गया है",
    "Session expired. Please login again.":"सेशन समाप्त हो गया है। कृपया फिर से लॉगिन करें।",
    "Network request failed":"नेटवर्क कनेक्शन उपलब्ध नहीं है",
    "New password and confirmation do not match.":"नया पासवर्ड और पुष्टि एक जैसी नहीं है।",
  };
  if(i18n.language !== "hi") return message;
  if(map[message]) return map[message];
  if(/^Request failed:/i.test(message)) return "अनुरोध पूरा नहीं हो सका। कृपया फिर से प्रयास करें।";
  if(/PDF/i.test(message)) return "PDF तैयार नहीं हो सकी। कृपया फिर से प्रयास करें।";
  return message;
};

// Core navigation/auth keys kept explicit for both locales.
Object.assign(hi, {
  "Search by Member ID/Name":"सदस्य आईडी/नाम से खोजें", "All status":"सभी स्थिति", "Active":"सक्रिय", "Inactive":"निष्क्रिय", "Secondary Phone":"दूसरा फोन", "Available":"उपलब्ध", "No address":"पता उपलब्ध नहीं", "Quick View":"त्वरित विवरण", "Choose an action":"कार्य चुनें", "Save Member":"सदस्य सहेजें", "Create Member":"सदस्य बनाएँ", "will be forced to change this temporary credential at next login.":"अगली लॉगिन पर यह अस्थायी क्रेडेंशियल बदलना अनिवार्य होगा।", "Search transactions":"लेन-देन खोजें", "entries":"प्रविष्टियाँ", "No note":"नोट नहीं", "View Details":"विवरण देखें", "Active shares":"सक्रिय शेयर", "Member actions":"सदस्य के कार्य", "Add Transaction / Entry":"लेन-देन / प्रविष्टि जोड़ें", "View Full Ledger / History":"पूरा लेजर / इतिहास देखें", "Passbook Entry":"पासबुक प्रविष्टि", "No proof":"प्रमाण उपलब्ध नहीं",
  installApp:"ऐप इंस्टॉल करें", more:"और विकल्प",
  login:"लॉगिन", phone:"फोन", password:"पिन / पासवर्ड", dashboard:"डैशबोर्ड", members:"सदस्य", register:"वित्तीय रजिस्टर", ledger:"सदस्य लेजर", analytics:"विश्लेषण", passbook:"व्यक्तिगत पासबुक", loans:"समूह ऋण", personalLoan:"व्यक्तिगत ऋण", admin:"एडमिन और ऑडिट", logout:"लॉगआउट", vault:"कुल वॉल्ट बैलेंस", cash:"कैश", bank:"बैंक बैलेंस", paid:"जमा", pending:"बाकी", save:"सहेजें", addMember:"सदस्य जोड़ें", contribution:"मासिक योगदान", expense:"खर्च", applyLoan:"अग्रिम ऋण के लिए आवेदन", language:"भाषा", noData:"अभी डेटा नहीं है", tagline:"आपकी बचत, आपका विकास", appName:"भारत बचत"
});
Object.assign(hi, {
  "Primary navigation":"मुख्य नेविगेशन", "Close":"बंद करें", "Group Admin":"समूह एडमिन", "Member":"सदस्य", "Super Admin":"सुपर एडमिन",
  "per share / month":"प्रति शेयर / माह", "Operational financial overview":"वित्तीय संचालन का सारांश", "Inflows":"आवक", "Outflows":"जावक", "Inflow":"आवक", "Outflow":"जावक",
  "Savings":"बचत", "Outstanding Loan":"बकाया ऋण", "Transactions":"लेन-देन", "Viewing":"देख रहे हैं", "Kist Status":"किस्त स्थिति", "paid shares":"जमा शेयर",
  "All active shares":"सभी सक्रिय शेयर", "Share code":"शेयर कोड", "Group code":"समूह कोड", "Code":"कोड", "Kist/share":"किस्त/शेयर",
  "Tenant ID":"टेनेंट आईडी", "Expected":"अपेक्षित", "Paid":"जमा", "Principal":"मूलधन", "Interest":"ब्याज", "Active":"सक्रिय", "Inactive":"निष्क्रिय",
  "Create Group":"समूह बनाएँ", "Create Admin":"एडमिन बनाएँ", "Logo":"लोगो", "Deactivate":"निष्क्रिय करें", "Activate":"सक्रिय करें",
  "Address":"पता", "Create BC Group":"BC समूह बनाएँ", "Create Group Admin":"समूह एडमिन बनाएँ", "Edit Member":"सदस्य संपादित करें", "Email":"ईमेल",
  "First Name":"पहला नाम", "Group / Tenant Name":"समूह / टेनेंट नाम", "Last Name":"अंतिम नाम", "Member-Wise Register":"सदस्य-वार रजिस्टर",
  "Monthly Kist / Share":"मासिक किस्त / शेयर", "Months":"महीने", "Number of Shares":"शेयरों की संख्या", "Opening Bank":"प्रारंभिक बैंक बैलेंस", "Opening Cash":"प्रारंभिक कैश बैलेंस",
  "Payment Date":"भुगतान तारीख", "Primary Phone":"मुख्य फोन", "Purpose":"उद्देश्य", "Requested Amount":"अनुरोधित राशि", "Reset Group Admin Password":"समूह एडमिन पासवर्ड रीसेट करें",
  "Reset Member PIN / Password":"सदस्य पिन / पासवर्ड रीसेट करें", "Super Admin Control":"सुपर एडमिन नियंत्रण", "Temporary PIN / Password":"अस्थायी पिन / पासवर्ड",
  "Unique Group Code":"विशिष्ट समूह कोड", "e.g. Stationery":"जैसे स्टेशनरी", "Search by Member ID/Name":"सदस्य नाम/फोन से खोजें", "All status":"सभी स्थिति",
  "Secondary Phone":"दूसरा फोन", "Available":"उपलब्ध", "No address":"पता उपलब्ध नहीं", "Quick View":"त्वरित विवरण", "Choose an action":"कार्य चुनें",
  "Save Member":"सदस्य सहेजें", "Create Member":"सदस्य बनाएँ", "Search transactions":"लेन-देन खोजें", "entries":"प्रविष्टियाँ", "No note":"नोट नहीं",
  "View Details":"विवरण देखें", "Active shares":"सक्रिय शेयर", "Member actions":"सदस्य के कार्य", "Add Transaction / Entry":"लेन-देन / प्रविष्टि जोड़ें",
  "View Full Ledger / History":"पूरा लेजर / इतिहास देखें", "Passbook Entry":"पासबुक प्रविष्टि", "No proof":"प्रमाण उपलब्ध नहीं", "Latest Activity":"हाल की गतिविधियां",
  "View Activity / गतिविधियां देखें":"गतिविधियां देखें", "Previous":"पिछला", "Next":"अगला", "Year":"वर्ष", "Share":"शेयर", "All Years":"सभी वर्ष", "All Shares":"सभी शेयर",
  "Group Overall Summary":"समूह का संपूर्ण सारांश", "Your personal data with the consolidated group position":"आपके व्यक्तिगत डेटा के साथ समूह की समेकित स्थिति", "Group Savings":"समूह बचत", "My Savings":"मेरी बचत", "My Activity":"मेरी गतिविधि",
  "Cash Inflow":"कैश आवक", "Cash Outflow":"कैश जावक", "Personal Profile":"व्यक्तिगत जानकारी", "Savings & Shares Ledger":"बचत एवं शेयर लेजर",
  "Loan & EMI Status":"ऋण एवं किश्त स्थिति", "Activity / History":"गतिविधियां / इतिहास", "Member Details":"सदस्य विवरण", "Back to Members":"सदस्यों पर वापस जाएँ",
  "Total Shares":"कुल शेयर", "Status":"स्थिति", "Loan":"ऋण", "No purpose":"उद्देश्य नहीं", "No active loans":"कोई सक्रिय ऋण नहीं",
  "Analytics":"विश्लेषण", "Total Contributions":"कुल योगदान", "Monthly Collection vs Expenses":"मासिक संग्रह बनाम खर्च", "My Savings Growth":"मेरी बचत वृद्धि", "Balance":"बैलेंस", "Interest and Repayment Trend":"ब्याज और ऋण-वापसी रुझान",
  "X-axis: Months · Y-axis: Amount in ₹":"X-अक्ष: महीने · Y-अक्ष: राशि ₹ में", "Settings":"सेटिंग्स", "Language":"भाषा", "Choose app language":"ऐप की भाषा चुनें",
  "Theme":"थीम", "Light or dark appearance":"लाइट या डार्क रूप", "Light":"लाइट", "Dark":"डार्क", "Primary Color":"मुख्य रंग", "Customize app accent":"ऐप का मुख्य रंग बदलें",
  "Notifications":"सूचनाएं", "Actions":"कार्य", "Profile":"प्रोफाइल", "Reset PIN":"पिन रीसेट करें", "Photo":"फोटो", "Remove Photo":"फोटो हटाएँ", "Security":"सुरक्षा", "Reset Password":"पासवर्ड रीसेट करें",
  "Kist Setting":"किस्त सेटिंग", "Group":"समूह", "Cash":"कैश", "Bank":"बैंक", "Date":"तारीख", "Select":"चुनें", "Edit":"संपादित करें",
  "A share cannot receive more than its configured monthly Kist for the selected month.":"चुने गए महीने में किसी शेयर के लिए निर्धारित मासिक किस्त से अधिक राशि दर्ज नहीं की जा सकती।",
  "Every Kist entry is linked to the exact share ID, so passbooks and analytics remain accurate.":"हर किस्त सही शेयर से जुड़ी रहती है, जिससे पासबुक और विश्लेषण सटीक रहते हैं।",
  "Expected {amount}":"अपेक्षित {amount}", "Paid {amount}":"जमा {amount}", "Share {number}":"शेयर {number}",
  "Global multi-tenant controller: groups and Group Admin accounts":"सभी समूहों और समूह एडमिन खातों का वैश्विक प्रबंधन",
  "Super Admin password itself is intentionally not reset from this UI. This reset is only for Group Admin accounts.":"सुपर एडमिन का पासवर्ड इस स्क्रीन से रीसेट नहीं किया जाता। यह रीसेट केवल समूह एडमिन खातों के लिए है।",
  "This amount becomes the expected monthly Kist for each active share. Historical transactions keep their original expected amount.":"यह राशि प्रत्येक सक्रिय शेयर की अपेक्षित मासिक किस्त होगी। पुराने लेन-देन अपनी मूल अपेक्षित राशि पर ही रहेंगे।"
});
Object.assign(en, { installApp:"Install App", more:"More", login:"Login", phone:"Phone", password:"PIN / Password", dashboard:"Dashboard", members:"Members", register:"Financial Register", ledger:"Member Ledger", analytics:"Analytics", passbook:"Personal Passbook", loans:"Group Loans", personalLoan:"Personal Loan", admin:"Admin & Audit", logout:"Logout", vault:"Total Vault Balance", cash:"Cash-in-Hand", bank:"Bank Balance", paid:"Paid", pending:"Pending", save:"Save", addMember:"Add Member", contribution:"Monthly Contribution", expense:"Expense", applyLoan:"Apply for Advance Loan", language:"Language", noData:"No data yet", tagline:"Aapki Bachat, Aapka Vikas", appName:"Bharat Bachat", "Primary navigation":"Primary navigation", "Close":"Close", "Latest Activity":"Latest Activity", "View Activity / गतिविधियां देखें":"View Activity", "Previous":"Previous", "Next":"Next", "All Years":"All Years", "All Shares":"All Shares", "Settings":"Settings", "Choose app language":"Choose app language", "Theme":"Theme", "Light or dark appearance":"Light or dark appearance", "Light":"Light", "Dark":"Dark", "Primary Color":"Primary Color", "Customize app accent":"Customize app accent", "Notifications":"Notifications", "Group Overall Summary":"Group Overall Summary", "My Savings":"My Savings", "My Activity":"My Activity", "Cash Inflow":"Cash Inflow", "Cash Outflow":"Cash Outflow", "Personal Profile":"Personal Profile", "Savings & Shares Ledger":"Savings & Shares Ledger", "Loan & EMI Status":"Loan & EMI Status", "Activity / History":"Activity / History", "Member Details":"Member Details", "Back to Members":"Back to Members", "Total Shares":"Total Shares", "Status":"Status", "No active loans":"No active loans", "Monthly Collection vs Expenses":"Monthly Collection vs Expenses", "My Savings Growth":"My Savings Growth", "Balance":"Balance", "Interest and Repayment Trend":"Interest and Repayment Trend", "X-axis: Months · Y-axis: Amount in ₹":"X-axis: Months · Y-axis: Amount in ₹" });


Object.assign(hi,{"Smart Savings & Group Finance":"स्मार्ट बचत एवं समूह वित्त","Install Bharat Bachat on this device":"इस डिवाइस पर भारत बचत इंस्टॉल करें","Install":"इंस्टॉल करें","System Record":"सिस्टम रिकॉर्ड","Total Contributions":"कुल योगदान","Contribution":"योगदान","contribution":"योगदान","expense":"खर्च","loan_request":"ऋण अनुरोध","activity":"गतिविधि","transaction":"लेन-देन","active":"सक्रिय","closed":"बंद","cancelled":"रद्द","Pending":"बाकी","Partial":"आंशिक","Collected":"संग्रह","Loan":"ऋण","Group Activity":"समूह गतिविधि","All shares":"सभी शेयर","Custom":"कस्टम","Credit/Debit":"जमा/निकासी","Running Balance":"चलता बैलेंस"});
Object.assign(en,{"Smart Savings & Group Finance":"Smart Savings & Group Finance","Install Bharat Bachat on this device":"Install Bharat Bachat on this device","Install":"Install","System Record":"System Record","Total Contributions":"Total Contributions","Contribution":"Contribution","contribution":"Contribution","expense":"Expense","loan_request":"Loan request","activity":"Activity","transaction":"Transaction","active":"Active","closed":"Closed","cancelled":"Cancelled","Partial":"Partial","Collected":"Collected","Loan":"Loan","Group Activity":"Group Activity","All shares":"All shares","Custom":"Custom","Credit/Debit":"Credit/Debit","Running Balance":"Running Balance"});
Object.assign(hi,{"Repaid":"चुकाया","Days remaining":"बाकी दिन","Principal left":"बाकी मूलधन","New":"नया","Month":"माह","Months":"महीने","Active Loan":"सक्रिय ऋण","Closed Loan":"बंद ऋण","Loan":"ऋण","System Record":"सिस्टम रिकॉर्ड","Apply for Advance Loan":"अग्रिम ऋण के लिए आवेदन","Interest / Penalty Inflow":"ब्याज / जुर्माना आवक","Payment date":"भुगतान तारीख"});
Object.assign(en,{"Repaid":"Repaid","Days remaining":"days remaining","Principal left":"principal left","New":"New","Month":"Month","Months":"months","Active Loan":"Active Loan","Closed Loan":"Closed Loan","Loan":"Loan","System Record":"System Record","Apply for Advance Loan":"Apply for Advance Loan","Interest / Penalty Inflow":"Interest / Penalty Inflow","Payment date":"Payment date"});

Object.assign(hi, {
  "All charts live only in Analytics":"सभी चार्ट केवल विश्लेषण स्क्रीन में उपलब्ध हैं",
  "Description":"विवरण",
  "Interest % / month":"ब्याज % / माह",
  "Member (optional)":"सदस्य (वैकल्पिक)",
  "Monthly Kist per Active Share":"प्रति सक्रिय शेयर मासिक किस्त",
  "New Expense Category":"नई खर्च श्रेणी",
  "One tenant record = one BC group. All group data carries this tenant_id.":"एक टेनेंट रिकॉर्ड = एक BC समूह। सभी समूह डेटा इसी tenant_id से जुड़ा है।",
  "Personal activity and savings growth":"व्यक्तिगत गतिविधि और बचत वृद्धि",
  "Post Loan Repayment":"ऋण वापसी दर्ज करें",
  "members":"सदस्य"
});
Object.assign(en, {
  "All charts live only in Analytics":"All charts live only in Analytics",
  "Description":"Description",
  "Interest % / month":"Interest % / month",
  "Member (optional)":"Member (optional)",
  "Monthly Kist per Active Share":"Monthly Kist per Active Share",
  "New Expense Category":"New Expense Category",
  "One tenant record = one BC group. All group data carries this tenant_id.":"One tenant record = one BC group. All group data carries this tenant_id.",
  "Personal activity and savings growth":"Personal activity and savings growth",
  "Post Loan Repayment":"Post Loan Repayment",
  "members":"members"
});

Object.assign(hi, {"Recent Activity":"हाल की गतिविधियाँ","Group and personal position with one unified activity feed":"समूह और व्यक्तिगत स्थिति एक ही गतिविधि सूची में","Personal":"व्यक्तिगत" ,"Group":"समूह","Show":"दिखाएँ","Hide":"छिपाएँ","Group Savings":"समूह बचत","Cash Inflow":"कैश आवक","Cash Outflow":"कैश जावक","More":"और विकल्प","Install App":"ऐप इंस्टॉल करें","View Full Ledger / History":"पूरा लेजर / इतिहास देखें","Add Transaction / Entry":"लेन-देन / प्रविष्टि जोड़ें","View Details":"विवरण देखें","View Activity":"गतिविधियां देखें","Personal Passbook":"व्यक्तिगत पासबुक","Share PDF":"पीडीएफ साझा करें","Credit":"जमा","Debit":"निकासी","Account":"खाता","Running Balance":"चलता बैलेंस","Month":"माह","Year":"वर्ष","Search by Member ID/Name":"सदस्य नाम/फोन से खोजें","Filter":"फ़िल्टर","All status":"सभी स्थिति","Group Activity Log":"समूह गतिविधि लॉग","Total Group Savings":"कुल समूह बचत","Aavak":"आवक","Javak":"जावक","Monthly Collection":"मासिक संग्रह","Expenses":"खर्च","Repayments":"ऋण वापसी","Interest + Penalty":"ब्याज + जुर्माना","Payments":"भुगतान","Audit Logs":"ऑडिट लॉग","Drag and drop":"खींचकर छोड़ें","Upload bill or receipt":"बिल या रसीद अपलोड करें"});
Object.assign(en, {"Recent Activity":"Recent Activity","Group and personal position with one unified activity feed":"Group and personal position with one unified activity feed","Personal":"Personal","Group":"Group","Show":"Show","Hide":"Hide","Group Savings":"Group Savings","Cash Inflow":"Cash Inflow","Cash Outflow":"Cash Outflow","More":"More","Install App":"Install App","View Full Ledger / History":"View Full Ledger / History","Add Transaction / Entry":"Add Transaction / Entry","View Details":"View Details","View Activity":"View Activity","Personal Passbook":"Personal Passbook","Share PDF":"Share PDF","Credit":"Credit","Debit":"Debit","Account":"Account","Running Balance":"Running Balance","Month":"Month","Year":"Year","Search by Member ID/Name":"Search by Member ID/Name","Filter":"Filter","All status":"All status","Group Activity Log":"Group Activity Log","Total Group Savings":"Total Group Savings","Aavak":"Inflow","Javak":"Outflow","Monthly Collection":"Monthly Collection","Expenses":"Expenses","Repayments":"Repayments","Interest + Penalty":"Interest + Penalty","Payments":"Payments","Audit Logs":"Audit Logs","Drag and drop":"Drag and drop","Upload bill or receipt":"Upload bill or receipt"});

Object.assign(hi,{"Home":"होम","Cash Book":"कैश बुक","Ledger":"लेजर","Passbook":"पासबुक","Loans":"ऋण","My Loan":"मेरा ऋण","Admin":"एडमिन","Profile Photo":"प्रोफ़ाइल फोटो","No personal financial records":"कोई व्यक्तिगत वित्तीय रिकॉर्ड नहीं है","Group Vault":"समूह वॉल्ट","Group Total":"समूह कुल","My Share":"मेरा हिस्सा","Closing Balance":"समापन बैलेंस","Opening + Current":"प्रारंभिक + वर्तमान","Quick View":"त्वरित दृश्य","Search transactions":"लेन-देन खोजें","entries":"प्रविष्टियाँ","Reference":"संदर्भ","No note":"कोई नोट नहीं","Member actions":"सदस्य कार्य","Active shares":"सक्रिय शेयर","View Loan & Profit Summary":"ऋण और लाभ सारांश देखें"});
Object.assign(en,{"Home":"Home","Cash Book":"Cash Book","Ledger":"Ledger","Passbook":"Passbook","Loans":"Loans","My Loan":"My Loan","Admin":"Admin","Profile Photo":"Profile Photo","No personal financial records":"No personal financial records","Group Vault":"Group Vault","Group Total":"Group Total","My Share":"My Share","Closing Balance":"Closing Balance","Opening + Current":"Opening + Current","Quick View":"Quick View","Search transactions":"Search transactions","entries":"entries","Reference":"Reference","No note":"No note","Member actions":"Member actions","Active shares":"Active shares","View Loan & Profit Summary":"View Loan & Profit Summary"});

export default i18n;
Object.assign(hi, {"Group totals, loan/profit, expenses and one recent activity feed":"समूह कुल, ऋण/लाभ, खर्च और एक हाल की गतिविधि सूची","Active Loans":"सक्रिय ऋण","Interest Collected":"वसूल ब्याज","Loan Repayments":"ऋण वापसी","My Inflow":"मेरी आवक","My Outflow":"मेरी जावक","My Balance":"मेरा बैलेंस","My Interest / Profit":"मेरा ब्याज / लाभ","My Loan Outstanding":"मेरा बकाया ऋण","My Transactions":"मेरे लेन-देन","Current Year":"वर्तमान वर्ष","View Transactions":"लेन-देन देखें","Hide Transactions":"लेन-देन छिपाएँ","No group loans yet":"अभी कोई समूह ऋण नहीं है","Total Loans":"कुल ऋण","Outstanding":"बकाया","No data yet":"अभी डेटा नहीं है"});
Object.assign(en, {"Group totals, loan/profit, expenses and one recent activity feed":"Group totals, loan/profit, expenses and one recent activity feed","Active Loans":"Active Loans","Interest Collected":"Interest Collected","Loan Repayments":"Loan Repayments","My Inflow":"My Inflow","My Outflow":"My Outflow","My Balance":"My Balance","My Interest / Profit":"My Interest / Profit","My Loan Outstanding":"My Loan Outstanding","My Transactions":"My Transactions","Current Year":"Current Year","View Transactions":"View Transactions","Hide Transactions":"Hide Transactions","No group loans yet":"No group loans yet","Total Loans":"Total Loans","Outstanding":"Outstanding","No data yet":"No data yet"});

Object.assign(en,{"View Detailed Breakdown":"View Detailed Breakdown","Transactions Count":"Transactions Count","Group Members":"Group Members","Group Shares":"Group Shares","Total":"Total"});
Object.assign(hi,{"View Detailed Breakdown":"विस्तृत विवरण देखें","Transactions Count":"लेन-देन की संख्या","Group Members":"समूह सदस्य","Group Shares":"समूह शेयर","Total":"कुल"});
Object.assign(en,{"+ Add new expense name":"+ Add new expense name","Choose existing":"Choose existing","My Profit":"My Profit","Profit includes group interest, penalties and other non-contribution income, less your share of expenses.":"Profit includes group interest, penalties and other non-contribution income, less your share of expenses."});
Object.assign(hi,{"+ Add new expense name":"+ नया खर्च नाम जोड़ें","Choose existing":"मौजूदा चुनें","My Profit":"मेरा लाभ","Profit includes group interest, penalties and other non-contribution income, less your share of expenses.":"लाभ में समूह का ब्याज, पेनल्टी और अन्य योगदान के अलावा आय शामिल है, जिसमें आपके हिस्से का खर्च घटाया जाता है।"});

Object.assign(en,{"My Interest":"My Interest","Group Expenses":"Group Expenses","My Interest / Profit":"My Interest"});
Object.assign(hi,{"My Interest":"मेरा ब्याज","Group Expenses":"समूह का खर्च","My Interest / Profit":"मेरा ब्याज"});
