// refine — Minimal STL stubs for CBMC bounded model checking.
//
// CBMC cannot parse real libc++/libstdc++ headers. These stubs provide
// just enough type and algorithm support for specification checking.
// The stubs are NOT functionally complete — they model structure and
// contracts, not full semantics.

// ---- Minimal STL stubs for CBMC (no system headers needed) ----
namespace std {

template<typename T>
class vector {
public:
    T* _data;
    unsigned int _size;
    vector() : _data(0), _size(0) {}
    unsigned int size() const { return _size; }
    bool empty() const { return _size == 0; }
    T& operator[](unsigned int i) { return _data[i]; }
    const T& operator[](unsigned int i) const { return _data[i]; }
    T& operator[](int i) { return _data[i]; }
    const T& operator[](int i) const { return _data[i]; }
    T& front() { return _data[0]; }
    T& back() { return _data[_size > 0 ? _size - 1 : 0]; }
    const T& front() const { return _data[0]; }
    const T& back() const { return _data[_size > 0 ? _size - 1 : 0]; }
    void push_back(const T&) { }
    void reserve(unsigned int) { }
    T* begin() { return _data; }
    T* end() { return _data + _size; }
    const T* begin() const { return _data; }
    const T* end() const { return _data + _size; }
};

template<typename T1, typename T2>
struct pair {
    T1 first;
    T2 second;
};

template<typename T>
T abs(T x) { return x < 0 ? -x : x; }

// Sorting check (bounded)
template<typename It>
bool is_sorted(It begin, It end) {
    if (begin == end) return true;
    It prev = begin;
    for (It it = begin + 1; it != end; ++it) {
        if (*it < *prev) return false;
        prev = it;
    }
    return true;
}

// Algorithms (templated predicates — works with function pointers)
template<typename It, typename Pred>
bool all_of(It begin, It end, Pred p) {
    for (It it = begin; it != end; ++it)
        if (!p(*it)) return false;
    return true;
}

template<typename It, typename Pred>
bool any_of(It begin, It end, Pred p) {
    for (It it = begin; it != end; ++it)
        if (p(*it)) return true;
    return false;
}

template<typename It, typename Pred>
bool none_of(It begin, It end, Pred p) {
    for (It it = begin; it != end; ++it)
        if (p(*it)) return false;
    return true;
}

template<typename It1, typename It2>
bool equal(It1 first1, It1 last1, It2 first2) {
    for (; first1 != last1; ++first1, ++first2)
        if (!(*first1 == *first2)) return false;
    return true;
}

template<typename It1, typename It2>
bool equal(It1 first1, It1 last1, It2 first2, It2 last2) {
    for (; first1 != last1 && first2 != last2; ++first1, ++first2)
        if (!(*first1 == *first2)) return false;
    return first1 == last1 && first2 == last2;
}

template<typename It, typename T>
It find(It begin, It end, const T& val) {
    for (It it = begin; it != end; ++it)
        if (*it == val) return it;
    return end;
}

template<typename It>
It adjacent_find(It begin, It end) {
    if (begin == end) return end;
    It prev = begin;
    for (It it = begin + 1; it != end; ++it) {
        if (*it == *prev) return prev;
        prev = it;
    }
    return end;
}

template<typename It>
It min_element(It begin, It end) {
    if (begin == end) return end;
    It best = begin;
    for (It it = begin + 1; it != end; ++it)
        if (*it < *best) best = it;
    return best;
}

template<typename It>
It max_element(It begin, It end) {
    if (begin == end) return end;
    It best = begin;
    for (It it = begin + 1; it != end; ++it)
        if (*it > *best) best = it;
    return best;
}

template<typename It, typename T>
int count(It begin, It end, const T& val) {
    int c = 0;
    for (It it = begin; it != end; ++it)
        if (*it == val) c++;
    return c;
}

template<typename It, typename Pred>
int count_if(It begin, It end, Pred p) {
    int c = 0;
    for (It it = begin; it != end; ++it)
        if (p(*it)) c++;
    return c;
}

template<typename It, typename T>
T accumulate(It begin, It end, T init) {
    for (It it = begin; it != end; ++it)
        init = init + *it;
    return init;
}

template<typename T>
struct not_equal_to {
    bool operator()(const T& a, const T& b) const { return a != b; }
};

template<typename T>
struct numeric_limits {
    static T min() { return (T)(-2147483647 - 1); }
    static T max() { return (T)2147483647; }
    static T lowest() { return min(); }
};

// sort stub — nondeterministic for contract checking
template<typename It>
void sort(It begin, It end) { }

} // namespace std

// cmath stubs
double sqrt(double x) { double r; __CPROVER_assume(r >= 0 && r * r == x); return r; }
double pow(double base, double exp) { double r; return r; }
double fabs(double x) { return x >= 0 ? x : -x; }
double abs(double x) { return x >= 0 ? x : -x; }
int abs(int x) { return x >= 0 ? x : -x; }
double floor(double x) { double r; __CPROVER_assume(r <= x && r + 1 > x); return r; }
double ceil(double x) { double r; __CPROVER_assume(r >= x && r - 1 < x); return r; }
double round(double x) { double r; return r; }
double log(double x) { double r; return r; }
double log2(double x) { double r; return r; }

typedef unsigned int size_t;
typedef void* nullptr_t;
#define nullptr ((void*)0)
#define M_PI 3.14159265358979323846
#define EPSILON 0.0001

namespace std {
template<typename T>
void swap(T& a, T& b) { T tmp = a; a = b; b = tmp; }

template<typename T1, typename T2, typename T3>
struct tuple {
    T1 _0; T2 _1; T3 _2;
};

using ::pow;
using ::sqrt;
using ::abs;
using ::fabs;
using ::floor;
using ::ceil;
using ::round;
using ::log;
using ::log2;
}
// ---- End STL stubs ----
